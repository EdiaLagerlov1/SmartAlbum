#!/bin/bash

set -e

echo "========================================="
echo "SmartAlbum AWS Infrastructure Setup"
echo "========================================="

REGION="us-east-1"
BUCKET_NAME="smartalbum-photos-bucket"
TABLE_NAME="SmartAlbum-Albums"

echo ""
echo "Configuration:"
echo "  Region: $REGION"
echo "  S3 Bucket: $BUCKET_NAME"
echo "  DynamoDB Table: $TABLE_NAME"
echo ""

# Check if AWS CLI is installed
if ! command -v aws &> /dev/null; then
    echo "Error: AWS CLI is not installed. Please install it first."
    exit 1
fi

# Check AWS credentials
echo "Checking AWS credentials..."
if ! aws sts get-caller-identity &> /dev/null; then
    echo "Error: AWS credentials not configured properly."
    exit 1
fi
echo "✓ AWS credentials valid"

# Create S3 bucket
echo ""
echo "Creating S3 bucket: $BUCKET_NAME..."
if aws s3 ls "s3://$BUCKET_NAME" 2>&1 | grep -q 'NoSuchBucket'; then
    aws s3api create-bucket \
        --bucket "$BUCKET_NAME" \
        --region "$REGION" 2>&1 || true

    # Enable versioning
    aws s3api put-bucket-versioning \
        --bucket "$BUCKET_NAME" \
        --versioning-configuration Status=Enabled

    # Set lifecycle rule for PDF expiration (24 hours)
    cat > /tmp/lifecycle.json <<EOF
{
    "Rules": [
        {
            "ID": "DeleteOldPDFs",
            "Status": "Enabled",
            "Prefix": "albums/",
            "Expiration": {
                "Days": 1
            }
        }
    ]
}
EOF
    aws s3api put-bucket-lifecycle-configuration \
        --bucket "$BUCKET_NAME" \
        --lifecycle-configuration file:///tmp/lifecycle.json

    # Enable CORS
    cat > /tmp/cors.json <<EOF
{
    "CORSRules": [
        {
            "AllowedHeaders": ["*"],
            "AllowedMethods": ["GET", "PUT", "POST", "DELETE"],
            "AllowedOrigins": ["*"],
            "ExposeHeaders": []
        }
    ]
}
EOF
    aws s3api put-bucket-cors \
        --bucket "$BUCKET_NAME" \
        --cors-configuration file:///tmp/cors.json

    rm /tmp/lifecycle.json /tmp/cors.json

    echo "✓ S3 bucket created: $BUCKET_NAME"
else
    echo "✓ S3 bucket already exists: $BUCKET_NAME"
fi

# Create DynamoDB table
echo ""
echo "Creating DynamoDB table: $TABLE_NAME..."
if ! aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$REGION" &> /dev/null; then
    aws dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions \
            AttributeName=albumId,AttributeType=S \
        --key-schema \
            AttributeName=albumId,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region "$REGION" \
        --tags Key=Application,Value=SmartAlbum

    echo "Waiting for table to be active..."
    aws dynamodb wait table-exists --table-name "$TABLE_NAME" --region "$REGION"

    # Enable TTL
    aws dynamodb update-time-to-live \
        --table-name "$TABLE_NAME" \
        --time-to-live-specification "Enabled=true,AttributeName=expiresAt" \
        --region "$REGION"

    echo "✓ DynamoDB table created: $TABLE_NAME"
else
    echo "✓ DynamoDB table already exists: $TABLE_NAME"
fi

echo ""
echo "========================================="
echo "✓ AWS Infrastructure Setup Complete!"
echo "========================================="
echo ""
echo "Resources created:"
echo "  - S3 Bucket: $BUCKET_NAME"
echo "  - DynamoDB Table: $TABLE_NAME"
echo ""
echo "Next steps:"
echo "  1. Build the application: mvn clean install"
echo "  2. Run with Docker: docker-compose up --build"
echo "  3. Access frontend: http://localhost"
echo ""
