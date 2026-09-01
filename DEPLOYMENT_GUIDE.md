# AWS Infrastructure Deployment Guide

## Prerequisites

Before deploying, ensure you have:

1. **Terraform** (>= 1.0)
   - Download: https://www.terraform.io/downloads
   - Verify: `terraform version`

2. **AWS CLI** (v2)
   - Download: https://aws.amazon.com/cli/
   - Verify: `aws --version`

3. **AWS Credentials Configured**
   ```bash
   aws configure
   ```
   - Region: `us-west-2` (Seoul)
   - Access Key: Your AWS IAM user access key
   - Secret Key: Your AWS IAM user secret key

4. **Verify AWS Access**
   ```bash
   aws sts get-caller-identity
   ```

## Deployment Steps

### Step 1: Validate Terraform Configuration

```bash
cd infra/
terraform fmt          # Format code
terraform validate    # Validate syntax
```

### Step 2: Initialize Terraform

```bash
terraform init
```

This downloads required AWS provider plugins.

### Step 3: Review Infrastructure Plan

```bash
terraform plan -out=tfplan
```

This shows all resources that will be created:
- 3 S3 buckets (bronze, silver, gold)
- IAM roles and policies
- CloudWatch log groups
- CloudWatch alarms and metric filters

### Step 4: Apply Infrastructure

```bash
terraform apply tfplan
```

This creates all AWS resources. Takes ~2-3 minutes.

### Step 5: Capture Output Variables

After deployment completes, capture the outputs:

```bash
terraform output -json > aws-outputs.json
```

Or export environment variables:

```bash
export AWS_REGION=$(terraform output -raw aws_region)
export AWS_S3_BRONZE_BUCKET=$(terraform output -raw bronze_bucket_name)
export AWS_S3_SILVER_BUCKET=$(terraform output -raw silver_bucket_name)
export AWS_S3_GOLD_BUCKET=$(terraform output -raw gold_bucket_name)
export AWS_IAM_ROLE_ARN=$(terraform output -raw airflow_role_arn)
export AWS_CLOUDWATCH_LOG_GROUP=$(terraform output -raw airflow_log_group_name)
```

## Integration with Local Airflow

### Step 1: Update .env File

Copy the Terraform outputs to your `airflow_config/.env` file:

```bash
# airflow_config/.env - Add these lines
AWS_REGION=us-west-2
AWS_S3_BRONZE_BUCKET=pipeline-pjt-dev-bronze-ACCOUNT_ID
AWS_S3_SILVER_BUCKET=pipeline-pjt-dev-silver-ACCOUNT_ID
AWS_S3_GOLD_BUCKET=pipeline-pjt-dev-gold-ACCOUNT_ID
AWS_IAM_ROLE_ARN=arn:aws:iam::ACCOUNT_ID:role/pipeline-pjt-dev-airflow
AWS_CLOUDWATCH_LOG_GROUP=/aws/airflow/pipeline-pjt-dev
```

### Step 2: Configure AWS CLI in Docker

Update `docker-compose.yml` volumes section to mount AWS credentials:

```yaml
airflow-webserver:
  volumes:
    - ~/.aws:/home/airflow/.aws:ro  # Add this line
    - ./:/pipeline
    - ./dags:/pipeline/dags
    - ./logs:/pipeline/logs
    - ./airflow_config:/pipeline/config

airflow-scheduler:
  volumes:
    - ~/.aws:/home/airflow/.aws:ro  # Add this line
    - ./:/pipeline
    - ./dags:/pipeline/dags
    - ./logs:/pipeline/logs
    - ./airflow_config:/pipeline/config
```

### Step 3: Update requirements.txt

Add boto3 for AWS SDK support:

```bash
# Add to requirements.txt
boto3==1.33.6
botocore==1.33.6
```

Then rebuild Docker image:

```bash
docker-compose build
```

### Step 4: Upload Bronze Data to S3

```bash
# Get account ID from terraform outputs
ACCOUNT_ID=$(terraform output -raw aws_account_id)
BRONZE_BUCKET=$(terraform output -raw bronze_bucket_name)

# Sync Bronze data to S3
aws s3 sync outputs/bronze_merged/ \
  s3://${BRONZE_BUCKET}/ \
  --region us-west-2
```

### Step 5: Restart Airflow

```bash
docker-compose down
docker-compose up -d
```

Verify Airflow is running:
```bash
docker-compose logs -f airflow-webserver
```

Access at: http://localhost:8080

## Monitoring

### View CloudWatch Logs

```bash
LOG_GROUP=$(terraform output -raw airflow_log_group_name)
aws logs tail ${LOG_GROUP} --follow --region us-west-2
```

### Check S3 Buckets

```bash
ACCOUNT_ID=$(terraform output -raw aws_account_id)

aws s3 ls s3://pipeline-pjt-dev-bronze-${ACCOUNT_ID}/
aws s3 ls s3://pipeline-pjt-dev-silver-${ACCOUNT_ID}/
aws s3 ls s3://pipeline-pjt-dev-gold-${ACCOUNT_ID}/
```

### Check CloudWatch Alarms

```bash
aws cloudwatch describe-alarms \
  --alarm-name-prefix "pipeline-pjt-dev" \
  --region us-west-2
```

## Cost Estimation

Monthly cost breakdown (estimated):

| Service | Cost | Notes |
|---------|------|-------|
| S3 Storage | $0.02-0.05 | ~1GB typical usage |
| CloudWatch Logs | $0.50 | 30-day retention |
| Data Transfer | $0.00 | Within region (free) |
| **Total** | **$0.50-0.55** | Very cost-effective |

## Rollback / Cleanup

To destroy all AWS resources:

```bash
cd infra/
terraform destroy
```

Confirm when prompted. This will delete:
- S3 buckets (with data)
- IAM roles and policies
- CloudWatch log groups and alarms

⚠️ **Warning**: Cannot undo. Data in S3 buckets will be deleted.

## Troubleshooting

### IAM Permission Error
```
Error: Error creating IAM role: AccessDenied
```
Solution: Ensure your AWS user has sufficient permissions (AdministratorAccess or custom policy with S3/IAM/CloudWatch access).

### S3 Bucket Already Exists
```
Error: Error creating S3 bucket: BucketAlreadyExists
```
Solution: Edit `infra/terraform.tfvars` and change `s3_bucket_prefix` to a unique name.

### CloudWatch Logs Not Appearing
- Verify Airflow container has AWS credentials mounted
- Check IAM role has `logs:PutLogEvents` permission
- View container logs: `docker-compose logs airflow-scheduler`

### Terraform State Issues
If you need to reset Terraform state:
```bash
# Backup current state
cp terraform.tfstate terraform.tfstate.backup

# Re-initialize
terraform init
terraform refresh
```

## Integration Workflow

```
1. Deploy Terraform Infrastructure
   ↓
2. Configure Airflow .env with AWS outputs
   ↓
3. Update docker-compose.yml with AWS credential mount
   ↓
4. Rebuild Docker image with boto3
   ↓
5. Upload Bronze data to S3
   ↓
6. Restart Airflow containers
   ↓
7. Monitor CloudWatch logs
   ↓
8. Trigger bronze_to_silver DAG
   ↓
9. Monitor Silver data in S3
```

## Next Steps

1. ✅ **Local Airflow Setup** - Already done
2. ✅ **Silver Layer DAG** - Already done
3. ⏳ **Deploy Terraform** - Ready to proceed
4. ⏳ **Upload Bronze Data** - After Terraform deployment
5. ⏳ **Configure Airflow Integration** - AWS credentials + environment
6. ⏳ **Create Gold Layer DAG** - Advanced analytics
7. ⏳ **Setup Lambda Functions** - Automated data processing
8. ⏳ **Configure Athena Queries** - SQL analysis

## Support

For detailed info, see:
- `README_TERRAFORM.md` - Infrastructure details
- `README_SILVER.md` - Airflow DAG documentation
- `infra/` folder - Individual Terraform files
