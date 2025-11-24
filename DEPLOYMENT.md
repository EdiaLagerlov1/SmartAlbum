# SmartAlbum Deployment Summary

## ✅ Successfully Deployed!

Your SmartAlbum application has been deployed to AWS.

### 🌐 Public Frontend URL
**http://smartalbum-frontend-503561418496.s3-website-us-east-1.amazonaws.com**

### 🖥️ Backend Services (EC2)
- **EC2 Instance ID**: i-023794c76da5b3c2e
- **Public IP**: 3.82.21.171
- **Instance Type**: t2.micro (FREE tier)

Services should be running on:
- Upload Service: http://3.82.21.171:8080
- PDF Service: http://3.82.21.171:8082
- Download Service: http://3.82.21.171:8083

### ⚠️ IMPORTANT NOTE

The EC2 instance was launched without an IAM role, so it cannot download the JAR files from S3 automatically.

**To complete the setup, you need to:**

1. **Connect to EC2** (using AWS Console or SSH)
2. **Manually start the services** OR **attach an IAM role**

### Option A: SSH and Manual Setup

```bash
# SSH to instance (requires your key pair)
ssh -i your-key.pem ec2-user@3.82.21.171

# Once connected:
sudo yum install -y java-17-amazon-corretto

# Download JARs (these are public in your S3 bucket)
wget https://smartalbum-photos-bucket.s3.amazonaws.com/deploy/upload-service-1.0.0.jar
wget https://smartalbum-photos-bucket.s3.amazonaws.com/deploy/pdf-service-1.0.0.jar
wget https://smartalbum-photos-bucket.s3.amazonaws.com/deploy/download-service-1.0.0.jar

# Start services
nohup java -jar upload-service-1.0.0.jar > upload.log 2>&1 &
nohup java -jar pdf-service-1.0.0.jar > pdf.log 2>&1 &
nohup java -jar download-service-1.0.0.jar > download.log 2>&1 &

# Check services are running
ps aux | grep java
```

### Option B: Attach IAM Role (via AWS Console)

1. Go to EC2 Console → Instances
2. Select instance `i-023794c76da5b3c2e`
3. Actions → Security → Modify IAM Role
4. Attach a role with S3 and DynamoDB permissions
5. Reboot instance (services will auto-start via user data)

### 📦 AWS Resources Created

- **S3 Buckets**:
  - `smartalbum-photos-bucket` (images & PDFs)
  - `smartalbum-frontend-503561418496` (static website)

- **DynamoDB Table**: `SmartAlbum-Albums`

- **EC2 Instance**: `SmartAlbum-Server` (i-023794c76da5b3c2e)

- **Security Group**: `SmartAlbum-SG` (sg-074ccb050eb1da981)
  - Allows ports: 8080, 8082, 8083

### 💰 Cost Estimate

**$0.00/month** - Everything within FREE tier:
- EC2 t2.micro: 750 hours/month free
- S3: 5GB storage free
- DynamoDB: 25GB storage free

### 🧹 Cleanup

To delete everything:

```bash
# Stop and terminate EC2 instance
aws ec2 terminate-instances --instance-ids i-023794c76da5b3c2e

# Run cleanup script
./scripts/cleanup-aws.sh

# Delete frontend bucket
aws s3 rb s3://smartalbum-frontend-503561418496 --force

# Delete security group
aws ec2 delete-security-group --group-id sg-074ccb050eb1da981
```

### 📝 Next Steps

1. **Complete EC2 setup** (see options above)
2. **Test your app** at the frontend URL
3. **Monitor costs** in AWS Billing Console
4. **Remember** to stop EC2 when not in use to maximize free tier

---

**Need help?** Check README.md for full documentation.
