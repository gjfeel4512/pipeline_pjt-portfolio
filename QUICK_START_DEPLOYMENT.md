# 🚀 Quick Start: AWS Infrastructure Deployment

## Pre-Deployment Checklist

### ✅ Prerequisites
- [ ] Terraform installed (`terraform version`)
- [ ] AWS CLI installed (`aws --version`)
- [ ] AWS credentials configured (`aws configure`)
- [ ] Access to us-west-2 region
- [ ] Docker installed (for local Airflow)
- [ ] ~/.aws/credentials file exists with valid AWS credentials

### ✅ File Verification
```bash
cd C:\Pipeline_pjt

# Verify Terraform files
ls infra/
# Should show: cloudwatch.tf  iam.tf  locals.tf  main.tf  outputs.tf  provider.tf  s3.tf  terraform.tfvars  variables.tf

# Verify deployment script
ls scripts/
# Should show: deploy.sh

# Verify updated files
ls requirements.txt
ls docker-compose-aws.yml
ls dags/bronze_to_silver_dag_aws.py
```

---

## 📋 Deployment Steps

### Step 1: Verify AWS Credentials

```bash
# Test AWS CLI access
aws sts get-caller-identity

# Expected output:
# {
#     "UserId": "AIDAI...",
#     "Account": "827913617635",
#     "Arn": "arn:aws:iam::827913617635:user/your-user"
# }
```

**If failed:**
- Run: `aws configure` and enter your credentials
- Ensure you have S3, IAM, CloudWatch permissions

---

### Step 2: Initialize Terraform

```bash
cd C:\Pipeline_pjt\infra

# Download AWS provider
terraform init

# Expected output should show:
# Terraform has been successfully configured for this backend!
```

**If you see errors:**
- Delete `.terraform/` folder and try again
- Or check internet connection (downloads from registry.terraform.io)

---

### Step 3: Validate Configuration

```bash
# Format terraform files
terraform fmt

# Validate syntax
terraform validate

# Expected output:
# Success! The configuration is valid.
```

---

### Step 4: Review Infrastructure Plan

```bash
# Generate plan
terraform plan -out=tfplan

# This will show all resources to be created:
# - 3 S3 buckets (bronze, silver, gold)
# - IAM role for Airflow
# - CloudWatch log groups and alarms
# - Estimated cost (usually ~$3-5/month)

# Review the plan output carefully!
```

---

### Step 5: Deploy Infrastructure ⚡

```bash
# Apply the plan (takes ~2-3 minutes)
terraform apply tfplan

# Watch the progress:
# aws_s3_bucket.bronze: Creating...
# aws_s3_bucket.silver: Creating...
# aws_s3_bucket.gold: Creating...
# aws_iam_role.airflow: Creating...
# ...
# Apply complete! Resources: 15 added, 0 changed, 0 destroyed.
```

**Success indicators:**
- No errors shown
- All resources created
- Outputs displayed at the end

---

### Step 6: Capture AWS Outputs

After successful deployment, capture the output values:

```bash
# View all outputs
terraform output

# Export as environment variables
$buckets = terraform output -json | ConvertFrom-Json

# Capture these values:
$ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text)
$AWS_REGION = "us-west-2"
$AWS_S3_BRONZE_BUCKET = terraform output -raw bronze_bucket_name
$AWS_S3_SILVER_BUCKET = terraform output -raw silver_bucket_name
$AWS_S3_GOLD_BUCKET = terraform output -raw gold_bucket_name
$AWS_IAM_ROLE_ARN = terraform output -raw airflow_role_arn
$AWS_CLOUDWATCH_LOG_GROUP = terraform output -raw airflow_log_group_name

# Verify
echo "Bucket: $AWS_S3_SILVER_BUCKET"
echo "Account: $ACCOUNT_ID"
```

---

### Step 7: Update Environment Variables

```bash
cd C:\Pipeline_pjt

# Update airflow_config/.env with your values
# Edit: airflow_config/.env
```

**Add/update these lines:**
```env
# AWS Configuration
AWS_REGION=us-west-2
AWS_S3_BRONZE_BUCKET=goldline-dev-bronze-827913617635
AWS_S3_SILVER_BUCKET=goldline-dev-silver-827913617635
AWS_S3_GOLD_BUCKET=goldline-dev-gold-827913617635
AWS_IAM_ROLE_ARN=arn:aws:iam::827913617635:role/goldline-dev-airflow
AWS_CLOUDWATCH_LOG_GROUP=/aws/airflow/goldline-dev
AWS_DEFAULT_REGION=us-west-2
```

---

### Step 8: Prepare Airflow Docker Environment

```bash
cd C:\Pipeline_pjt

# Update docker-compose.yml with new version (includes AWS credential mounting)
# Option A: Replace your docker-compose.yml with docker-compose-aws.yml
copy docker-compose-aws.yml docker-compose.yml

# Option B: Manual edit - Add AWS credential mounting to docker-compose.yml:
#   volumes:
#     - ~/.aws:/home/airflow/.aws:ro  # Add this line in both services
```

---

### Step 9: Rebuild and Start Airflow

```bash
cd C:\Pipeline_pjt

# Rebuild Docker image with new dependencies
docker-compose build

# Start all services
docker-compose up -d

# Verify services are running
docker-compose ps

# Expected output:
# CONTAINER ID   IMAGE      COMMAND                 STATUS
# xxx            postgres   "postgres"              Up (healthy)
# xxx            pipeline   "airflow webserver"     Up (healthy)
# xxx            pipeline   "airflow scheduler"     Up (healthy)
```

**Access Airflow:**
- URL: http://localhost:8080
- Username: airflow
- Password: airflow

---

### Step 10: Upload Bronze Data to S3

```bash
# Verify Bronze data exists locally
ls outputs/bronze_merged/

# Upload to S3 (replace BUCKET_NAME with your actual bucket)
aws s3 sync outputs/bronze_merged/ `
  s3://goldline-dev-bronze-827913617635/ `
  --region us-west-2 `
  --storage-class STANDARD

# Verify upload
aws s3 ls s3://goldline-dev-bronze-827913617635/
```

---

### Step 11: Activate DAG in Airflow

```
1. Open http://localhost:8080
2. Find DAG: "bronze_to_silver_with_s3"
3. Click toggle button to enable it
4. Wait 2 minutes for scheduler to sync
5. DAG appears in active list
```

---

### Step 12: Monitor Execution

**Watch CloudWatch logs:**
```bash
aws logs tail /aws/airflow/goldline-dev --follow
```

**Check S3 buckets:**
```bash
# Bronze (input)
aws s3 ls s3://goldline-dev-bronze-827913617635/

# Silver (transformed output)
aws s3 ls s3://goldline-dev-silver-827913617635/
```

**View Airflow logs:**
```bash
docker-compose logs -f airflow-scheduler
docker-compose logs -f airflow-webserver
```

---

## 🔍 Verification Checklist

After deployment, verify everything is working:

- [ ] Terraform apply completed successfully
- [ ] S3 buckets created in AWS (check AWS Console)
- [ ] IAM role created with proper permissions
- [ ] CloudWatch log groups exist
- [ ] `.env` file updated with AWS values
- [ ] `docker-compose.yml` updated (or using `-aws` version)
- [ ] Airflow containers running (`docker-compose ps`)
- [ ] Airflow accessible at http://localhost:8080
- [ ] Bronze data uploaded to S3
- [ ] `bronze_to_silver_with_s3` DAG visible in Airflow
- [ ] DAG activated (toggle enabled)
- [ ] Test DAG run successful (check Silver output in S3)

---

## ⚠️ Troubleshooting

### Issue: Terraform init fails
```bash
# Solution 1: Check internet connection
# Solution 2: Delete and reinitialize
cd infra/
rm -r .terraform/
terraform init
```

### Issue: AWS credentials not found
```bash
# Solution: Configure credentials
aws configure

# Verify:
aws sts get-caller-identity
```

### Issue: S3 bucket already exists
```bash
# Solution: Change bucket prefix in terraform.tfvars
# Edit: infra/terraform.tfvars
# Change: s3_bucket_prefix = "youtube-data-v2"
# Then: terraform apply
```

### Issue: Airflow can't access S3
```bash
# Check AWS credential mounting:
docker-compose exec airflow-webserver ls -la ~/.aws/

# Verify credentials file:
cat ~/.aws/credentials
```

### Issue: CloudWatch logs not appearing
```bash
# Check IAM permissions:
aws iam get-role-policy --role-name goldline-dev-airflow --policy-name goldline-dev-airflow-logs

# Check CloudWatch log groups:
aws logs describe-log-groups --log-group-name-prefix /aws/airflow/
```

---

## 📊 Expected Cost

Monthly estimate for typical usage:

| Service | Cost |
|---------|------|
| S3 Storage (1GB) | $0.023 |
| CloudWatch Logs (100MB/month) | $0.05 |
| Data Transfer (within region) | $0.00 |
| **Total** | **~$0.07/month** |

Extremely cost-effective! ✨

---

## 📚 Next Steps

1. ✅ **Deploy AWS Infrastructure** ← You are here
2. ✅ **Configure Local Airflow**
3. ⏳ **Run Bronze → Silver transformation**
4. ⏳ **Create Silver → Gold DAG**
5. ⏳ **Setup Lambda for data collection**
6. ⏳ **Configure Athena for SQL queries**

---

## 🆘 Getting Help

If you encounter issues:

1. Check logs:
   ```bash
   docker-compose logs -f
   ```

2. Check AWS CloudWatch:
   ```bash
   aws logs tail /aws/airflow/goldline-dev --follow
   ```

3. Review Terraform state:
   ```bash
   terraform state list
   terraform state show aws_s3_bucket.silver
   ```

4. Rollback (if needed):
   ```bash
   terraform destroy
   # Confirm when prompted
   ```

---

## 📖 Documentation

- `DEPLOYMENT_GUIDE.md` - Detailed deployment steps
- `README_TERRAFORM.md` - Terraform infrastructure details
- `README_SILVER.md` - Silver layer transformation logic
- `infra/` folder - Individual Terraform files
