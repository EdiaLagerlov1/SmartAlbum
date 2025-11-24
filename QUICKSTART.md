# SmartAlbum - Quick Start Guide

## What You Have

A complete serverless photo album application with:

✅ **3 Microservices** (Java Spring Boot)
- Upload Service (Port 8080)
- PDF Generation Service (Port 8082)
- Download Service (Port 8083)

✅ **Frontend** (HTML/CSS/JavaScript)
- Beautiful web interface
- File upload with validation
- Progress tracking
- Download functionality

✅ **AWS Integration**
- S3 for storage
- DynamoDB for metadata
- Automatic provisioning scripts

✅ **Docker Support**
- Individual Dockerfiles per service
- Docker Compose orchestration
- One-command deployment

## Get Started in 2 Steps

### Step 1: Setup AWS (one-time)

```bash
./scripts/setup-aws.sh
```

This creates your S3 bucket and DynamoDB table (Free Tier).

### Step 2: Run the Application

```bash
./start.sh
```

Or manually:

```bash
docker-compose up --build
```

### Step 3: Use It!

Open your browser: **http://localhost**

1. Enter an album name
2. Upload photos (JPG/PNG)
3. Click "Generate Album"
4. Download your PDF!

## Architecture

```
┌─────────────┐
│  Frontend   │ (Port 80)
│   (Nginx)   │
└──────┬──────┘
       │
       ├──────────────┬──────────────┬────────────┐
       │              │              │            │
       v              v              v            v
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌─────┐
│  Upload  │──>│   PDF    │   │ Download │   │ S3  │
│ Service  │   │ Service  │   │ Service  │   └─────┘
│  :8080   │   │  :8082   │   │  :8083   │       ^
└──────────┘   └──────────┘   └──────────┘       │
       │              │              │            │
       └──────────────┴──────────────┴────────────┘
                      │
                      v
              ┌──────────────┐
              │  DynamoDB    │
              └──────────────┘
```

## Project Structure

```
SmartAlbum/
├── shared-lib/              # Common code (Album, S3Service, DynamoDB)
├── upload-service/          # Handles photo uploads
├── pdf-service/            # Generates PDF albums
├── download-service/       # Provides download URLs
├── frontend/               # Web UI (HTML/CSS/JS)
├── scripts/
│   ├── setup-aws.sh       # Creates AWS resources
│   └── cleanup-aws.sh     # Deletes AWS resources
├── docker-compose.yml      # Orchestrates all services
├── start.sh               # One-command startup
├── prd.md                 # Product requirements
└── README.md              # Full documentation
```

## Troubleshooting

**Docker issues?**
```bash
docker-compose down
docker system prune -a
docker-compose up --build
```

**AWS credentials?**
Check `~/.aws/credentials` exists with:
```
[default]
aws_access_key_id = YOUR_KEY
aws_secret_access_key = YOUR_SECRET
region = us-east-1
```

**Services not connecting?**
```bash
docker-compose ps
docker-compose logs upload-service
```

## Testing Without Docker

```bash
# Build
mvn clean install

# Run each service in separate terminals
cd upload-service && mvn spring-boot:run
cd pdf-service && mvn spring-boot:run
cd download-service && mvn spring-boot:run
cd frontend && python3 -m http.server 80
```

## What's Included

- ✅ Input validation (file type, size, count)
- ✅ Detailed logging for all operations
- ✅ Error handling and status tracking
- ✅ Automatic TTL (7 days for data, 24h for PDFs)
- ✅ CORS enabled for frontend
- ✅ Presigned URLs for secure downloads
- ✅ Files under 150 lines (per requirements)
- ✅ Single responsibility principle
- ✅ AWS Free Tier compatible

## Cleanup

When done:
```bash
./scripts/cleanup-aws.sh
```

⚠️ **Warning**: Deletes all albums and data!

## Next Steps

1. Test the application with sample photos
2. Check AWS Console to see S3/DynamoDB resources
3. Monitor logs: `docker-compose logs -f`
4. Customize frontend styling in `frontend/style.css`
5. Add features (see prd.md for scope)

## Support

- Full documentation: `README.md`
- Requirements: `prd.md`
- Issues: Check service logs

Enjoy your SmartAlbum! 📸
