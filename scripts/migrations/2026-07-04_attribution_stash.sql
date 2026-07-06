-- Attribution stash + atomic single-use claim + retention policy
-- Owner: Atlas (backend). Consumed by Indra's Next.js /r/ (write) + /api/attribution-claim (claim).
-- Purpose: deferred iOS UTM attribution. /r/ click stashes utm+ip; first_open POSTs
-- fingerprint; we match on IP + time window, atomically single-use claim, return utm.
--
-- Privacy: ip is PII. It is NULLED the instant a row is claimed, and unclaimed rows
-- (which can never match after the window) are purged after 24h by pg_cron. Only
-- in-flight unclaimed rows inside the match window ever hold an IP. Reverse at bottom.

create table if not exists public.attribution_stash (
  id            uuid primary key default gen_random_uuid(),
  -- click-time capture (written by the /r/ handler, iOS clicks only)
  ip            text,            -- nullable: set on click, NULLED on claim (PII minimization)
  slug          text,
  utm_source    text,
  utm_medium    text,
  utm_campaign  text,
  utm_term      text,
  utm_content   text,
  gclid         text,
  fbclid        text,
  ua            text,            -- raw user-agent at click (coarse fingerprint)
  accept_language text,          -- maps to client locale prefix
  created_at    timestamptz not null default now(),
  -- claim state (single-use)
  claimed_at        timestamptz,
  claim_fingerprint jsonb        -- what the claim matched on, for audit
);

create index if not exists idx_attr_stash_created on public.attribution_stash (created_at);
-- hot path: unclaimed rows for an IP inside the window
create index if not exists idx_attr_stash_ip_open on public.attribution_stash (ip, created_at) where claimed_at is null;

alter table public.attribution_stash enable row level security;
-- No anon/authenticated policies: only the service role (server routes) touches this.

-- Atomic match + single-use claim. The /api/attribution-claim route calls it with
-- the service key (bypasses RLS). Ambiguous IP (carrier NAT) -> no-match, so a wrong
-- claim never burns the real stash row. The raw IP is dropped on claim.
create or replace function public.claim_attribution(
  p_ip           text,
  p_device_model text default null,
  p_os_version   text default null,
  p_locale       text default null,
  p_client_ts    timestamptz default null,
  p_window_mins  int default 60
) returns table (
  matched boolean,
  utm_source text, utm_medium text, utm_campaign text,
  utm_term text, utm_content text, gclid text, fbclid text
)
language plpgsql
as $$
declare
  v_id uuid;
  v_count int;
begin
  -- count unclaimed candidates: same IP, inside window, locale-compatible if sent
  select count(*) into v_count
  from public.attribution_stash s
  where s.claimed_at is null
    and s.ip = p_ip
    and s.created_at >= now() - make_interval(mins => p_window_mins)
    and (p_locale is null or s.accept_language is null
         or s.accept_language ilike left(p_locale, 2) || '%');

  -- zero candidates OR ambiguous (>1 behind same NAT) -> honest no-match
  if v_count <> 1 then
    return query select false, null::text, null::text, null::text,
                        null::text, null::text, null::text, null::text;
    return;
  end if;

  -- exactly one: lock it, claim it atomically (SKIP LOCKED blocks double-claim)
  select s.id into v_id
  from public.attribution_stash s
  where s.claimed_at is null
    and s.ip = p_ip
    and s.created_at >= now() - make_interval(mins => p_window_mins)
  order by s.created_at desc
  limit 1
  for update skip locked;

  if v_id is null then
    return query select false, null::text, null::text, null::text,
                        null::text, null::text, null::text, null::text;
    return;
  end if;

  update public.attribution_stash
     set claimed_at = now(),
         ip = null,   -- PII minimization: raw IP has served its purpose once matched
         claim_fingerprint = jsonb_build_object(
           'device_model', p_device_model, 'os_version', p_os_version,
           'locale', p_locale, 'client_ts', p_client_ts)
   where id = v_id;

  return query
    select true, s.utm_source, s.utm_medium, s.utm_campaign,
           s.utm_term, s.utm_content, s.gclid, s.fbclid
    from public.attribution_stash s where s.id = v_id;
end;
$$;

grant execute on function public.claim_attribution(text,text,text,text,timestamptz,int) to service_role;

-- Retention: unclaimed rows can never match after the window (delete >24h); claimed
-- rows already have no IP, purge >7d to drop the residual fingerprint. Returns count.
create or replace function public.purge_attribution_stash() returns integer
language plpgsql security definer set search_path = public as $$
declare n integer;
begin
  delete from public.attribution_stash
   where (claimed_at is null and created_at < now() - interval '24 hours')
      or (claimed_at is not null and claimed_at < now() - interval '7 days');
  get diagnostics n = row_count;
  return n;
end;
$$;

-- Schedule hourly (idempotent). Requires pg_cron (installed on this project).
select cron.unschedule('purge-attribution-stash')
  where exists (select 1 from cron.job where jobname = 'purge-attribution-stash');
select cron.schedule('purge-attribution-stash', '0 * * * *',
  $$select public.purge_attribution_stash()$$);

-- REVERSE:
-- select cron.unschedule('purge-attribution-stash');
-- drop function if exists public.purge_attribution_stash();
-- drop function if exists public.claim_attribution(text,text,text,text,timestamptz,int);
-- drop table if exists public.attribution_stash;
