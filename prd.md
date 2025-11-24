# Product Requirements Document: SmartAlbum

## 1. Overview
A serverless web application that converts user photos into a downloadable PDF photo album with 2 images per page.

## 2. Goals
- Allow users to upload photos
- Collect album name from user
- Generate PDF with 2 images per page
- Provide PDF download to user

## 3. User Flow
1. User opens web application
2. User enters album name
3. User uploads multiple photos
4. User clicks "Generate Album"
5. System processes photos and creates PDF
6. User downloads the generated PDF

## 4. Technical Stack
- **Backend**: Java (Spring Boot)
- **Infrastructure**: AWS Free Tier services
- **Containerization**: Docker
- **Architecture**: Microservices

## 5. AWS Services (Free Tier)
- **S3**: Store uploaded images, generated PDFs, and host static frontend
- **EC2**: t2.micro instance running Java microservices (750 hours/month free)
- **DynamoDB**: Store album metadata and job status
- **Security Group**: Control access to EC2 instance (ports 22, 8080, 8082, 8083)

## 6. System Components

### 6.1 Frontend (Static Web)
- Hosted on S3 static website hosting
- Single HTML page with upload form
- Input: Album name (text field)
- Input: Multiple photo upload (file input)
- Submit button
- Download link display
- Connects to EC2 backend services via REST API

### 6.2 Upload Service (Microservice #1)
**Responsibility**: Handle photo uploads
- Endpoint: POST /api/albums/upload
- Validate image files (format, size)
- Generate unique album ID
- Upload images to S3
- Store album metadata in DynamoDB
- Return album ID to frontend

### 6.3 PDF Generation Service (Microservice #2)
**Responsibility**: Generate PDF from uploaded images
- Triggered by: Upload completion
- Fetch images from S3
- Arrange 2 images per page
- Generate PDF using Java PDF library (Apache PDFBox)
- Upload PDF to S3
- Update album status in DynamoDB

### 6.4 Download Service (Microservice #3)
**Responsibility**: Provide PDF download
- Endpoint: GET /api/albums/{albumId}/download
- Verify album exists and PDF is ready
- Generate presigned S3 URL
- Return download URL to frontend

## 7. Data Model

### DynamoDB Table: Albums
```
{
  "albumId": "string (partition key)",
  "albumName": "string",
  "imageKeys": ["string array"],
  "pdfKey": "string",
  "status": "UPLOADING | PROCESSING | READY | FAILED",
  "createdAt": "timestamp",
  "expiresAt": "timestamp (TTL)"
}
```

## 8. S3 Bucket Structure
```
/albums/{albumId}/images/{imageId}.jpg
/albums/{albumId}/output/album.pdf
```

## 9. File Size Limits
- Maximum 150 lines per service file
- Each microservice follows single responsibility
- Common utilities extracted to shared library

## 10. Logging Requirements
- Upload Service: Log upload start, validation, S3 upload, completion
- PDF Generation Service: Log processing start, image fetch, PDF creation, upload
- Download Service: Log download requests, URL generation

## 11. Non-Functional Requirements
- Image formats: JPG, PNG
- Max images per album: 50
- Max image size: 5MB per image
- PDF auto-expires after 24 hours (S3 lifecycle)
- Album data TTL: 7 days

## 12. Deployment Architecture

### Production Environment
- **Frontend**: S3 static website (http://smartalbum-frontend-503561418496.s3-website-us-east-1.amazonaws.com)
- **Backend**: EC2 t2.micro instance (Public IP: 3.82.21.171)
- **Services**: Run as JAR files using nohup for persistence
- **Region**: us-east-1

### EC2 Configuration
- **Instance ID**: i-023794c76da5b3c2e
- **Instance Type**: t2.micro (free tier eligible)
- **AMI**: Amazon Linux 2023
- **Java Runtime**: Amazon Corretto 17
- **Open Ports**: 22 (SSH), 8080 (Upload), 8082 (PDF), 8083 (Download)

### Deployment Process
1. Build JAR files locally with Maven
2. Upload JARs to S3 bucket
3. Generate presigned URLs for EC2 download
4. Connect to EC2 via EC2 Instance Connect
5. Download JARs and start services with nohup
6. Configure AWS credentials in ~/.aws/credentials on EC2

### Service Management on EC2
```bash
# Start services
nohup java -jar upload-service.jar > /tmp/upload.log 2>&1 &
nohup java -jar pdf-service.jar > /tmp/pdf.log 2>&1 &
nohup java -jar download-service.jar > /tmp/download.log 2>&1 &

# Check running services
ps aux | grep java

# View logs
tail -f /tmp/upload.log
tail -f /tmp/pdf.log
tail -f /tmp/download.log
```

## 13. Out of Scope (Not Implemented)
- User authentication
- Album editing/deletion
- Image filters or editing
- Custom layouts (only 2 images per page)
- Payment processing
- Email notifications
- Social sharing
