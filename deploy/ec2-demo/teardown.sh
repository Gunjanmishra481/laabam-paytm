#!/bin/bash
# Removes everything the laabam-demo deploy created (2026-11-15), and nothing else: each resource is named by its
# exact id or name, and all of them are tagged project=laabam-demo. Uses your default AWS CLI profile.
#   bash deploy/ec2-demo/teardown.sh
# CloudFront must be disabled and finish deploying before it can be deleted (about 5-15 minutes); this script waits.
# KMS keys can't be deleted at once: they are scheduled for deletion after 7 days (the minimum) and are disabled now.
set -uo pipefail
export MSYS_NO_PATHCONV=1
R=ap-south-1
CF=E12CPAL4P5T88O
IID=i-06b3e745276ff4274
SG=sg-0ca8eff788e5b4be3
SIGN_KEY=arn:aws:kms:ap-south-1:806121397725:key/f1455806-4ce1-4189-a3ac-372a1458665a
AUDIT_KEY=arn:aws:kms:ap-south-1:806121397725:key/6a250b0b-6ae2-49d8-82c8-831e31c9f090
SECRET=laabam-demo/sarvam-api-key
ROLE=laabam-demo-ec2
REPO=laabam-demo

echo "1/7 CloudFront $CF: disable, wait, delete"
ETAG=$(aws cloudfront get-distribution-config --id $CF --query ETag --output text)
aws cloudfront get-distribution-config --id $CF --query DistributionConfig > /tmp/laabam-cf.json
python -c "import json;d=json.load(open('/tmp/laabam-cf.json'));d['Enabled']=False;json.dump(d,open('/tmp/laabam-cf.json','w'))" 2>/dev/null \
  || python3 -c "import json;d=json.load(open('/tmp/laabam-cf.json'));d['Enabled']=False;json.dump(d,open('/tmp/laabam-cf.json','w'))"
aws cloudfront update-distribution --id $CF --if-match "$ETAG" --distribution-config file:///tmp/laabam-cf.json >/dev/null
aws cloudfront wait distribution-deployed --id $CF
aws cloudfront delete-distribution --id $CF --if-match "$(aws cloudfront get-distribution --id $CF --query ETag --output text)"

echo "2/7 EC2 instance $IID (its encrypted volume goes with it)"
aws ec2 terminate-instances --region $R --instance-ids $IID >/dev/null
aws ec2 wait instance-terminated --region $R --instance-ids $IID

echo "3/7 security group $SG"
aws ec2 delete-security-group --region $R --group-id $SG

echo "4/7 instance profile and role $ROLE"
aws iam remove-role-from-instance-profile --instance-profile-name $ROLE --role-name $ROLE
aws iam delete-instance-profile --instance-profile-name $ROLE
aws iam delete-role-policy --role-name $ROLE --policy-name laabam-demo-runtime
aws iam delete-role --role-name $ROLE

echo "5/7 secret $SECRET (deleted now, no recovery window)"
aws secretsmanager delete-secret --region $R --secret-id $SECRET --force-delete-without-recovery >/dev/null

echo "6/7 ECR repository $REPO and its images"
aws ecr delete-repository --region $R --repository-name $REPO --force >/dev/null

echo "7/7 KMS keys: aliases removed, keys disabled and scheduled for deletion in 7 days"
aws kms delete-alias --region $R --alias-name alias/laabam-demo-tokens
aws kms delete-alias --region $R --alias-name alias/laabam-demo-audit
for k in $SIGN_KEY $AUDIT_KEY; do
  aws kms disable-key --region $R --key-id $k
  aws kms schedule-key-deletion --region $R --key-id $k --pending-window-in-days 7 >/dev/null
done
echo "done: laabam-demo removed"
