#!/usr/bin/env bash
# =============================================================================
# Smart Inventory - Lab helper (ทางลัดสำหรับคู่มือ)  รันใน AWS CloudShell
#   cd ~/CS446_Smart_Inventory
#   bash infra/lab.sh <step>
#
# steps:  check | dynamodb | email-test | lambda | test | fx-schedule | web | status | cleanup
# ทุกคำสั่ง AWS ที่สคริปต์รัน จะพิมพ์ให้เห็นเป็นสีฟ้า (เพื่อเรียนรู้ว่าทางลัดทำอะไร)
# =============================================================================
set -uo pipefail
export AWS_DEFAULT_REGION=us-east-1
export AWS_PAGER=""
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

C='\033[36m'; G='\033[32m'; R='\033[31m'; Y='\033[33m'; N='\033[0m'
run()  { echo -e "${C}\$ $*${N}"; "$@"; }
ok()   { echo -e "${G}✔ $*${N}"; }
warn() { echo -e "${Y}! $*${N}"; }
die()  { echo -e "${R}✘ $*${N}"; exit 1; }
hr()   { echo "------------------------------------------------------------"; }

ACC=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
  || die "เชื่อมต่อ AWS ไม่ได้ - กด Start Lab แล้วเปิด CloudShell ใหม่"
ROLE="arn:aws:iam::${ACC}:role/LabRole"
BUCKET="smartinv-web-${ACC}"
FUNCS="smartinv-api smartinv-checker smartinv-fx"

topic_arn() { aws sns list-topics --query "Topics[?ends_with(TopicArn, ':smartinv-alerts')].TopicArn" --output text 2>/dev/null; }
api_url()   { local u; u=$(aws lambda get-function-url-config --function-name smartinv-api --query FunctionUrl --output text 2>/dev/null) || return 1; echo "${u%/}"; }

# ---------------------------------------------------------------------------
step_check() {
  hr; echo "Account : $ACC"; echo "Region  : $AWS_DEFAULT_REGION"; echo "Role    : $ROLE"; hr
  aws iam get-role --role-name LabRole --query Role.RoleName --output text >/dev/null 2>&1 \
    && ok "พบ LabRole" || warn "อ่านข้อมูล LabRole ไม่ได้ (ไม่เป็นไร ถ้า Lab ปกติจะมีอยู่แล้ว)"
  [[ -f backend/lambda_api.py && -f frontend/index.html ]] && ok "พบโค้ด backend และ frontend" \
    || die "ไม่พบโค้ด - ต้องอยู่ในโฟลเดอร์ CS446_Smart_Inventory"
  ok "พร้อมเริ่ม Lab"
}

# ---------------------------------------------------------------------------
mk_table() {  # name pk [sk]
  local name=$1 pk=$2 sk=${3:-}
  if aws dynamodb describe-table --table-name "$name" >/dev/null 2>&1; then warn "ตาราง $name มีอยู่แล้ว (ข้าม)"; return; fi
  if [[ -n "$sk" ]]; then
    run aws dynamodb create-table --table-name "$name" --billing-mode PAY_PER_REQUEST \
      --attribute-definitions AttributeName=$pk,AttributeType=S AttributeName=$sk,AttributeType=S \
      --key-schema AttributeName=$pk,KeyType=HASH AttributeName=$sk,KeyType=RANGE \
      --query TableDescription.TableStatus --output text
  else
    run aws dynamodb create-table --table-name "$name" --billing-mode PAY_PER_REQUEST \
      --attribute-definitions AttributeName=$pk,AttributeType=S \
      --key-schema AttributeName=$pk,KeyType=HASH \
      --query TableDescription.TableStatus --output text
  fi
}
step_dynamodb() {
  mk_table Products sku
  mk_table Sales sku date
  mk_table Rates pair timestamp
  mk_table Alerts date timestamp
  echo "รอให้ตารางพร้อม..."
  for t in Products Sales Rates Alerts; do aws dynamodb wait table-exists --table-name $t; done
  aws dynamodb list-tables --query "TableNames[?contains(['Products','Sales','Rates','Alerts'], @)]" --output table
  ok "DynamoDB ครบ 4 ตาราง"
}

# ---------------------------------------------------------------------------
step_email_test() {
  local t; t=$(topic_arn); [[ -n "$t" ]] || die "ยังไม่มี SNS topic ชื่อ smartinv-alerts (ทำ Part 3 ก่อน)"
  local subs; subs=$(aws sns list-subscriptions-by-topic --topic-arn "$t" --query "Subscriptions[].[Endpoint,SubscriptionArn]" --output text)
  [[ -n "$subs" ]] || die "ยังไม่มีอีเมลสมัครรับ (ทำข้อ 3.2 ก่อน)"
  echo "$subs" | while read -r ep arn; do
    [[ "$arn" == PendingConfirmation ]] && warn "$ep ยังไม่ได้กด Confirm subscription" || ok "$ep ยืนยันแล้ว"
  done
  run aws sns publish --topic-arn "$t" --subject "Smart Inventory test" \
    --message "Hello from CloudShell - SNS works!" --query MessageId --output text
  ok "ส่งอีเมลทดสอบแล้ว - เช็ก Inbox (หรือ Spam) ภายใน 1 นาที"
}

# ---------------------------------------------------------------------------
step_lambda() {
  local t; t=$(topic_arn); [[ -n "$t" ]] || die "ยังไม่มี SNS topic smartinv-alerts (ทำ Part 3 ก่อน)"
  local zip=/tmp/backend.zip
  rm -f $zip
  (cd backend && run zip -q $zip core.py service.py store.py notify.py lambda_api.py lambda_checker.py lambda_fx.py) \
    || die "zip ไม่สำเร็จ"
  ok "รวมโค้ด 7 ไฟล์เป็น backend.zip"
  local env="{\"Variables\":{\"STORE\":\"dynamo\",\"SNS_TOPIC_ARN\":\"$t\"}}"
  for f in $FUNCS; do
    local h="lambda_${f#smartinv-}.lambda_handler"
    if aws lambda get-function --function-name "$f" >/dev/null 2>&1; then
      warn "$f มีอยู่แล้ว -> อัปเดตโค้ดและค่าตั้ง"
      run aws lambda update-function-code --function-name "$f" --zip-file fileb://$zip --query LastUpdateStatus --output text
      aws lambda wait function-updated-v2 --function-name "$f"
      run aws lambda update-function-configuration --function-name "$f" --handler "$h" --timeout 30 \
        --memory-size 256 --environment "$env" --query LastUpdateStatus --output text
    else
      run aws lambda create-function --function-name "$f" --runtime python3.12 --role "$ROLE" \
        --handler "$h" --zip-file fileb://$zip --timeout 30 --memory-size 256 \
        --environment "$env" --query State --output text
    fi
  done
  echo "รอให้ฟังก์ชันพร้อม..."
  for f in $FUNCS; do aws lambda wait function-active-v2 --function-name "$f"; done
  aws lambda list-functions --query "Functions[?starts_with(FunctionName,'smartinv')].[FunctionName,Handler,Timeout,MemorySize]" --output table
  ok "Lambda ครบ 3 ฟังก์ชัน"
}

# ---------------------------------------------------------------------------
step_test() {
  local api; api=$(api_url) || die "smartinv-api ยังไม่มี Function URL (ทำ Part 5 ก่อน)"
  echo "API = $api"
  echo -e "${C}\$ curl -X POST $api/reset${N}"
  local r; r=$(curl -s -X POST "$api/reset"); echo "$r"
  [[ "$r" == *Forbidden* ]] && die "403 Forbidden - Function URL ขาดสิทธิ์ lambda:InvokeFunction (ดู Troubleshooting)"
  echo -e "${C}\$ curl $api/dashboard${N}"
  curl -s "$api/dashboard" | python3 -c '
import json,sys
d=json.load(sys.stdin); r=d["rate"]
print("  สินค้าทั้งหมด :", d["summary"]["total"], "รายการ")
print("  ค่าเงิน USD/THB:", r["rate"], "(source:", r["source"]+",", "stale:", str(r["stale"])+")")
for p in d["products"]: print("   -", p["sku"], p["name"], "| stock", p["stock"], "|", p["status"])
' || die "อ่าน dashboard ไม่ได้ - ดู CloudWatch logs ของ smartinv-api"
  ok "API ทำงาน (Lambda + DynamoDB + Frankfurter)"
}

# ---------------------------------------------------------------------------
step_fx_schedule() {
  local fx; fx=$(aws lambda get-function --function-name smartinv-fx --query Configuration.FunctionArn --output text 2>/dev/null) \
    || die "ยังไม่มี smartinv-fx (ทำ Part 4 ก่อน)"
  if aws scheduler get-schedule --name smartinv-fx-1h >/dev/null 2>&1; then warn "smartinv-fx-1h มีอยู่แล้ว (ข้าม)"
  else
    run aws scheduler create-schedule --name smartinv-fx-1h --schedule-expression "rate(1 hour)" \
      --schedule-expression-timezone Asia/Bangkok --flexible-time-window Mode=OFF \
      --target "Arn=$fx,RoleArn=$ROLE" --query ScheduleArn --output text || die "สร้าง schedule ไม่สำเร็จ"
  fi
  aws scheduler list-schedules --name-prefix smartinv --query "Schedules[].[Name,State]" --output table
  ok "EventBridge Scheduler พร้อม"
}

# ---------------------------------------------------------------------------
step_web() {
  local api; api=$(api_url) || die "smartinv-api ยังไม่มี Function URL (ทำ Part 5 ก่อน)"
  if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then warn "bucket $BUCKET มีอยู่แล้ว (ข้าม)"
  else run aws s3api create-bucket --bucket "$BUCKET" --query Location --output text || die "สร้าง bucket ไม่สำเร็จ"; fi
  run aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false \
    || die "ปิด Block Public Access ไม่ได้"
  local policy="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"PublicReadGetObject\",\"Effect\":\"Allow\",\"Principal\":\"*\",\"Action\":\"s3:GetObject\",\"Resource\":\"arn:aws:s3:::$BUCKET/*\"}]}"
  run aws s3api put-bucket-policy --bucket "$BUCKET" --policy "$policy" || die "ใส่ bucket policy ไม่ได้"
  run aws s3 website "s3://$BUCKET" --index-document index.html
  sed "s#^let API_URL = \".*\";#let API_URL = \"$api\";#" frontend/index.html > /tmp/index.html
  grep -q "^let API_URL = \"$api\";" /tmp/index.html || die "ใส่ API_URL ลง index.html ไม่สำเร็จ"
  ok "ใส่ API_URL ลงหน้าเว็บแล้ว: $(grep -n '^let API_URL' /tmp/index.html)"
  run aws s3 cp /tmp/index.html "s3://$BUCKET/index.html" --content-type "text/html; charset=utf-8" --only-show-errors \
    || die "อัปโหลดไม่สำเร็จ"
  hr
  ok "เปิดเว็บได้ที่ (แนะนำ - https):"
  echo "   https://$BUCKET.s3.us-east-1.amazonaws.com/index.html"
  echo "   (สำรอง http: http://$BUCKET.s3-website-us-east-1.amazonaws.com )"
  hr
}

# ---------------------------------------------------------------------------
step_status() {
  hr; echo "สถานะ 5 บริการ - Account $ACC"; hr
  local n; n=$(aws dynamodb list-tables --query "length(TableNames[?contains(['Products','Sales','Rates','Alerts'], @)])" --output text 2>/dev/null)
  [[ "$n" == 4 ]] && ok "DynamoDB    : 4/4 ตาราง" || warn "DynamoDB    : $n/4 ตาราง"
  local t; t=$(topic_arn)
  if [[ -n "$t" ]]; then
    local c; c=$(aws sns list-subscriptions-by-topic --topic-arn "$t" --query "length(Subscriptions[?SubscriptionArn!='PendingConfirmation'])" --output text)
    [[ "$c" -ge 1 ]] && ok "SNS         : topic + $c อีเมลที่ยืนยันแล้ว" || warn "SNS         : มี topic แต่ยังไม่มีอีเมลที่ Confirm"
  else warn "SNS         : ไม่มี topic"; fi
  n=$(aws lambda list-functions --query "length(Functions[?starts_with(FunctionName,'smartinv')])" --output text)
  [[ "$n" == 3 ]] && ok "Lambda      : 3/3 ฟังก์ชัน" || warn "Lambda      : $n/3 ฟังก์ชัน"
  local api; api=$(api_url) && ok "FunctionURL : $api" || warn "FunctionURL : ยังไม่มี (smartinv-api)"
  n=$(aws scheduler list-schedules --name-prefix smartinv --query "length(Schedules[?State=='ENABLED'])" --output text 2>/dev/null)
  [[ "$n" == 2 ]] && ok "EventBridge : 2/2 schedules ENABLED" || warn "EventBridge : $n/2 schedules"
  if aws s3api head-object --bucket "$BUCKET" --key index.html >/dev/null 2>&1; then
    ok "S3          : https://$BUCKET.s3.us-east-1.amazonaws.com/index.html"
  else warn "S3          : ยังไม่มีหน้าเว็บ"; fi
  local last; last=$(aws logs describe-log-streams --log-group-name /aws/lambda/smartinv-checker --order-by LastEventTime \
    --descending --limit 1 --query "logStreams[0].lastEventTimestamp" --output text 2>/dev/null)
  [[ "$last" =~ ^[0-9]+$ ]] && echo "   checker ทำงานล่าสุด: $(TZ=Asia/Bangkok date -d @$((last/1000)) '+%d/%m %H:%M:%S') (เวลาไทย)"
  hr
}

# ---------------------------------------------------------------------------
step_cleanup() {
  echo -e "${R}จะลบทรัพยากร Smart Inventory ทั้งหมดในบัญชี $ACC${N}"
  read -r -p "พิมพ์ yes เพื่อยืนยัน: " a; [[ "$a" == yes ]] || { echo "ยกเลิก"; exit 0; }
  for s in smartinv-check-5min smartinv-fx-1h; do
    run aws scheduler delete-schedule --name $s 2>/dev/null && ok "ลบ schedule $s" || warn "ไม่พบ schedule $s"
  done
  for r in smartinv-check-5min smartinv-fx-1h; do   # เผื่อสร้างแบบ Rule (legacy)
    aws events remove-targets --rule $r --ids 1 >/dev/null 2>&1; aws events delete-rule --name $r >/dev/null 2>&1 && ok "ลบ rule $r"
  done
  for f in $FUNCS; do
    run aws lambda delete-function --function-name $f 2>/dev/null && ok "ลบ Lambda $f (รวม Function URL)" || warn "ไม่พบ $f"
  done
  for t in Products Sales Rates Alerts; do
    run aws dynamodb delete-table --table-name $t --query TableDescription.TableStatus --output text 2>/dev/null \
      && ok "ลบตาราง $t" || warn "ไม่พบตาราง $t"
  done
  local t; t=$(topic_arn)
  [[ -n "$t" ]] && run aws sns delete-topic --topic-arn "$t" && ok "ลบ SNS topic (รวม subscription)" || warn "ไม่พบ SNS topic"
  if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    run aws s3 rb "s3://$BUCKET" --force >/dev/null && ok "ลบ bucket $BUCKET"
  else warn "ไม่พบ bucket $BUCKET (ถ้าตั้งชื่ออื่น ให้ลบใน S3 Console)"; fi
  for f in $FUNCS; do aws logs delete-log-group --log-group-name /aws/lambda/$f 2>/dev/null && ok "ลบ log group /aws/lambda/$f"; done
  hr; ok "Clean up เสร็จ - ตรวจใน Console อีกครั้ง แล้ว Sign out และ End Lab"
}

case "${1:-}" in
  check) step_check ;;
  dynamodb) step_dynamodb ;;
  email-test) step_email_test ;;
  lambda) step_lambda ;;
  test) step_test ;;
  fx-schedule) step_fx_schedule ;;
  web) step_web ;;
  status) step_status ;;
  cleanup) step_cleanup ;;
  *) echo "ใช้: bash infra/lab.sh {check|dynamodb|email-test|lambda|test|fx-schedule|web|status|cleanup}"; exit 1 ;;
esac
