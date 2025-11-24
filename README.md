# SmartAlbum - Photo Album Generator

A serverless web application that transforms user photos into print-ready photo albums (PDF format).

## 🚀 Live Demo

**Try it now**: http://smartalbum-frontend-503561418496.s3-website-us-east-1.amazonaws.com

## Features

- Upload multiple photos (JPG, PNG)
- Automatic PDF generation with 2 images per page
- Download generated PDF albums
- AWS Free Tier compatible
- Microservices architecture with Docker

## Architecture

### Microservices

1. **Upload Service** (Port 8080)
   - Handles photo uploads
   - Validates images
   - Stores images in S3
   - Creates album metadata in DynamoDB

2. **PDF Service** (Port 8082)
   - Generates PDF from uploaded images
   - Arranges 2 images per page
   - Stores PDF in S3
   - Updates album status

3. **Download Service** (Port 8083)
   - Generates presigned S3 URLs
   - Provides PDF download links

### AWS Services

- **S3**: Store images, PDFs, and host static frontend
- **DynamoDB**: Store album metadata
- **EC2**: t2.micro instance running backend services
- **Security Group**: Control access to EC2 (ports 22, 8080, 8082, 8083)
- **Region**: us-east-1

### Production Deployment

- **Frontend URL**: http://smartalbum-frontend-503561418496.s3-website-us-east-1.amazonaws.com
- **Backend EC2**: 3.82.21.171
- **Instance ID**: i-023794c76da5b3c2e

## Prerequisites

- Java 17 or higher
- Maven 3.6+
- Docker and Docker Compose
- AWS CLI configured with credentials
- AWS Free Tier account

## Quick Start

### 1. Setup AWS Infrastructure

```bash
cd scripts
./setup-aws.sh
```

This creates:
- S3 bucket: `smartalbum-photos-bucket`
- DynamoDB table: `SmartAlbum-Albums`

### 2. Build and Run with Docker

```bash
# Build and start all services
docker-compose up --build

# Or run in detached mode
docker-compose up -d --build
```

### 3. Access Application

**Local**: `http://localhost`
**Production**: http://smartalbum-frontend-503561418496.s3-website-us-east-1.amazonaws.com

## AWS Deployment

### Prerequisites for AWS Deployment
- AWS CLI configured with valid credentials
- EC2 instance running (t2.micro free tier)
- S3 bucket created with static website hosting enabled
- Security Group configured with required ports

### Deploy to AWS

#### 1. Build JAR files

```bash
mvn clean package
```

This creates:
- `upload-service/target/upload-service-1.0.0.jar`
- `pdf-service/target/pdf-service-1.0.0.jar`
- `download-service/target/download-service-1.0.0.jar`

#### 2. Upload JARs to S3

```bash
aws s3 cp upload-service/target/upload-service-1.0.0.jar s3://smartalbum-photos-bucket/deploy/
aws s3 cp pdf-service/target/pdf-service-1.0.0.jar s3://smartalbum-photos-bucket/deploy/
aws s3 cp download-service/target/download-service-1.0.0.jar s3://smartalbum-photos-bucket/deploy/
```

#### 3. Deploy Frontend to S3

```bash
aws s3 sync frontend/ s3://smartalbum-frontend-503561418496/ --exclude ".*"
```

#### 4. Start Services on EC2

Connect to EC2 instance via EC2 Instance Connect or SSH, then:

```bash
# Configure AWS credentials (one time)
mkdir -p ~/.aws
echo '[default]' > ~/.aws/credentials
echo 'aws_access_key_id = YOUR_ACCESS_KEY' >> ~/.aws/credentials
echo 'aws_secret_access_key = YOUR_SECRET_KEY' >> ~/.aws/credentials
echo '[default]' > ~/.aws/config
echo 'region = us-east-1' >> ~/.aws/config
chmod 600 ~/.aws/credentials ~/.aws/config

# Download and start services
cd /home/ec2-user
wget https://smartalbum-photos-bucket.s3.amazonaws.com/deploy/upload-service-1.0.0.jar -O upload-service.jar
wget https://smartalbum-photos-bucket.s3.amazonaws.com/deploy/pdf-service-1.0.0.jar -O pdf-service.jar
wget https://smartalbum-photos-bucket.s3.amazonaws.com/deploy/download-service-1.0.0.jar -O download-service.jar

nohup java -jar upload-service.jar > /tmp/upload.log 2>&1 &
sleep 5
nohup java -jar pdf-service.jar > /tmp/pdf.log 2>&1 &
sleep 5
nohup java -jar download-service.jar > /tmp/download.log 2>&1 &
sleep 5

# Verify services are running
ps aux | grep java
```

#### 5. Check Service Logs

```bash
tail -f /tmp/upload.log
tail -f /tmp/pdf.log
tail -f /tmp/download.log
```

## Manual Build (Without Docker)

### Build all services

```bash
mvn clean install
```

### Run services individually

```bash
# Terminal 1 - Upload Service
cd upload-service
mvn spring-boot:run

# Terminal 2 - PDF Service
cd pdf-service
mvn spring-boot:run

# Terminal 3 - Download Service
cd download-service
mvn spring-boot:run

# Terminal 4 - Frontend (requires a simple HTTP server)
cd frontend
python3 -m http.server 80
```

## API Endpoints

### Upload Service (Port 8080)

**Upload Album**
```
POST /api/albums/upload
Content-Type: multipart/form-data

Parameters:
- albumName: string
- images: file[] (max 50 files, 5MB each)

Response:
{
  "albumId": "uuid",
  "status": "PROCESSING",
  "message": "Album upload successful"
}
```

**Check Status**
```
GET /api/albums/status/{albumId}

Response:
{
  "albumId": "uuid",
  "albumName": "string",
  "status": "UPLOADING|PROCESSING|READY|FAILED"
}
```

### PDF Service (Port 8082)

**Generate PDF**
```
POST /api/pdf/generate
Content-Type: application/json

Body:
{
  "albumId": "uuid"
}

Response:
{
  "albumId": "uuid",
  "status": "PDF generation started"
}
```

### Download Service (Port 8083)

**Get Download URL**
```
GET /api/albums/{albumId}/download

Response:
{
  "albumId": "uuid",
  "downloadUrl": "presigned-s3-url",
  "expiresIn": "3600 seconds"
}
```

## Configuration

All services use AWS credentials from `~/.aws/credentials`

### Limits

- Max images per album: 50
- Max image size: 5MB
- Supported formats: JPG, PNG
- PDF expires: 24 hours (S3 lifecycle)
- Album data TTL: 7 days

## Project Structure

```
SmartAlbum/
├── shared-lib/          # Common utilities and models
├── upload-service/      # Image upload microservice
├── pdf-service/         # PDF generation microservice
├── download-service/    # Download microservice
├── frontend/            # Web UI
├── scripts/             # AWS setup/cleanup scripts
├── docker-compose.yml   # Docker orchestration
└── pom.xml             # Parent Maven configuration
```

## Cleanup

To delete all AWS resources:

```bash
cd scripts
./cleanup-aws.sh
```

⚠️ **Warning**: This will permanently delete all data!

## Development

### File Length Policy

Each file is kept under 150 lines following single responsibility principle.

### Logging

Each service logs:
- Request/response details
- S3 operations
- DynamoDB operations
- Errors and exceptions

### Architecture Principles

- Single Responsibility
- No duplicate code
- Separation of concerns
- Detailed logging

## Troubleshooting

### Docker build fails

```bash
# Clean Docker cache
docker-compose down
docker system prune -a
docker-compose up --build
```

### AWS credentials not found on EC2

Ensure `~/.aws/credentials` exists on EC2 instance:
```bash
cat ~/.aws/credentials
```

If missing, create it:
```bash
mkdir -p ~/.aws
echo '[default]' > ~/.aws/credentials
echo 'aws_access_key_id = YOUR_ACCESS_KEY' >> ~/.aws/credentials
echo 'aws_secret_access_key = YOUR_SECRET_KEY' >> ~/.aws/credentials
chmod 600 ~/.aws/credentials
```

Then restart services:
```bash
pkill -f 'java -jar'
cd /home/ec2-user
nohup java -jar upload-service.jar > /tmp/upload.log 2>&1 &
sleep 5
nohup java -jar pdf-service.jar > /tmp/pdf.log 2>&1 &
sleep 5
nohup java -jar download-service.jar > /tmp/download.log 2>&1 &
```

### Connection refused errors on frontend

Check if EC2 services are running:
```bash
# From your local machine
nc -zv 3.82.21.171 8080
nc -zv 3.82.21.171 8082
nc -zv 3.82.21.171 8083
```

If ports are not responding, connect to EC2 and check:
```bash
ps aux | grep java
tail -f /tmp/upload.log
```

### Cannot connect to EC2 via EC2 Instance Connect

Ensure port 22 is open in security group:
```bash
aws ec2 describe-security-groups --group-names SmartAlbum-SG
```

### Services stopped after EC2 reboot

Services don't auto-start on reboot. Manually restart them:
```bash
# Connect to EC2 and run
cd /home/ec2-user
nohup java -jar upload-service.jar > /tmp/upload.log 2>&1 &
sleep 5
nohup java -jar pdf-service.jar > /tmp/pdf.log 2>&1 &
sleep 5
nohup java -jar download-service.jar > /tmp/download.log 2>&1 &
```

### Local Docker: Services can't connect

Check all services are running:
```bash
docker-compose ps
```

Check service logs:
```bash
docker-compose logs upload-service
docker-compose logs pdf-service
docker-compose logs download-service
```

## Cost Considerations

This application is designed for AWS Free Tier:

- **EC2**: 750 hours/month of t2.micro (enough for 1 instance running 24/7)
- **S3**: 5GB storage, 20,000 GET requests, 2,000 PUT requests/month
- **DynamoDB**: 25GB storage, 25 WCU, 25 RCU (enough for thousands of albums)
- **Data Transfer**: 100GB out per month

**Important Notes:**
- Keep only 1 EC2 instance running to stay within free tier
- After 12 months, EC2 free tier expires (~ $8-10/month for t2.micro)
- S3 and DynamoDB free tier is permanent
- Monitor your usage in AWS Console to avoid unexpected charges

**To minimize costs:**
- Stop EC2 instance when not in use (services won't be available)
- Enable S3 lifecycle rules to delete old PDFs and images
- Use DynamoDB TTL to auto-delete expired albums

## License

MIT License

## Support

For issues and questions, please check the documentation or create an issue.
