#!/bin/bash
# Removes everything the laabam-beta deploy created (2026-11-15), and nothing else: each resource by its exact id or
# name, all tagged project=laabam-beta. Uses your default AWS CLI profile.
#   bash deploy/ec2-beta/teardown.sh
# The beta holds real owners' data. The database is NOT deleted without a final snapshot (laabam-beta-final-<date>),
# which you can restore or delete later. CloudFront takes 5-15 minutes to disable; the script waits.
set -uo pipefail
export MSYS_NO_PATHCONV=1
R=ap-south-1
CF=E12DDCUFOBSTUS
IID=i-07692fda100ab5e87
APPSG=sg-07031bfc19e6d5f4b
DBSG=sg-0abb22dcc08fb689c
DB=laabam-beta
ROLE=laabam-beta-ec2
REPO=laabam-beta
KEYS="arn:aws:kms:ap-south-1:806121397725:key/d170718f-034b-4aa2-a7fe-d8390bcf200e arn:aws:kms:ap-south-1:806121397725:key/f60f5703-6154-4b05-b047-34e086d8fbb9 arn:aws:kms:ap-south-1:806121397725:key/f7151453-5d62-4826-8916-66e75b10b558"

echo "1/8 CloudFront $CF: disable, wait, delete"
ETAG=$(aws cloudfront get-distribution-config --id $CF --query ETag --output text)
aws cloudfront get-distribution-config --id $CF --query DistributionConfig > /tmp/laabam-beta-cf.json
python -c "import json;d=json.load(open('/tmp/laabam-beta-cf.json'));d['Enabled']=False;json.dump(d,open('/tmp/laabam-beta-cf.json','w'))" 2>/dev/null   || python3 -c "import json;d=json.load(open('/tmp/laabam-beta-cf.json'));d['Enabled']=False;json.dump(d,open('/tmp/laabam-beta-cf.json','w'))"
aws cloudfront update-distribution --id $CF --if-match "$ETAG" --distribution-config file:///tmp/laabam-beta-cf.json >/dev/null
aws cloudfront wait distribution-deployed --id $CF
aws cloudfront delete-distribution --id $CF --if-match "$(aws cloudfront get-distribution --id $CF --query ETag --output text)"

echo "2/8 EC2 instance $IID"
aws ec2 terminate-instances --region $R --instance-ids $IID >/dev/null
aws ec2 wait instance-terminated --region $R --instance-ids $IID

echo "3/8 database $DB: deletion protection off, final snapshot, delete (waits ~10 min)"
aws rds modify-db-instance --region $R --db-instance-identifier $DB --no-deletion-protection --apply-immediately >/dev/null
aws rds wait db-instance-available --region $R --db-instance-identifier $DB
aws rds delete-db-instance --region $R --db-instance-identifier $DB   --final-db-snapshot-identifier "laabam-beta-final-$(date +%Y%m%d%H%M)" --delete-automated-backups >/dev/null
aws rds wait db-instance-deleted --region $R --db-instance-identifier $DB
aws rds delete-db-subnet-group --region $R --db-subnet-group-name laabam-beta

echo "4/8 security groups"
aws ec2 delete-security-group --region $R --group-id $DBSG
aws ec2 delete-security-group --region $R --group-id $APPSG

echo "5/8 instance profile and role $ROLE"
aws iam remove-role-from-instance-profile --instance-profile-name $ROLE --role-name $ROLE
aws iam delete-instance-profile --instance-profile-name $ROLE
aws iam delete-role-policy --role-name $ROLE --policy-name laabam-beta-runtime
aws iam delete-role --role-name $ROLE

echo "6/8 secrets (the database's master secret is removed by RDS with the database)"
for s in laabam-beta/sarvam-api-key laabam-beta/db-app; do
  aws secretsmanager delete-secret --region $R --secret-id $s --force-delete-without-recovery >/dev/null
done

echo "7/8 ECR repository $REPO and its images"
aws ecr delete-repository --region $R --repository-name $REPO --force >/dev/null

echo "8/8 KMS keys: aliases removed, keys disabled and scheduled for deletion in 7 days"
for a in alias/laabam-beta-tokens alias/laabam-beta-audit alias/laabam-beta-phone; do aws kms delete-alias --region $R --alias-name $a; done
for k in $KEYS; do
  aws kms disable-key --region $R --key-id $k
  aws kms schedule-key-deletion --region $R --key-id $k --pending-window-in-days 7 >/dev/null
done
echo "done: laabam-beta removed (the final database snapshot is kept until you delete it)"
