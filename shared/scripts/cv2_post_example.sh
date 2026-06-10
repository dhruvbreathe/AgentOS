#!/usr/bin/env bash
# Components V2 webhook post with attached chart. Usage:
#   WEBHOOK_URL=$MY_WEBHOOK_URL ./cv2_post_example.sh /path/to/chart.png /path/to/payload.json
# payload.json must use flags:32768 and reference the chart as attachment://<basename>.
set -euo pipefail
CHART="$1"; PAYLOAD="$2"
curl -sS -X POST "${WEBHOOK_URL}?with_components=true" \
  -A "VayuAgentOS/1.0 (+https://vayu-prana.com)" \
  -F "payload_json=<${PAYLOAD}" \
  -F "file1=@${CHART}" \
  -w "\nHTTP %{http_code}\n"
