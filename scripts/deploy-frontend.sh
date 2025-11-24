#!/bin/bash

set -e

echo "========================================="
echo "SmartAlbum Frontend Deployment to S3"
echo "========================================="

REGION="us-east-1"
BUCKET_NAME="smartalbum-frontend"

# Get your account ID for unique bucket name
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
UNIQUE_BUCKET="${BUCKET_NAME}-${ACCOUNT_ID}"

echo ""
echo "Configuration:"
echo "  Region: $REGION"
echo "  S3 Bucket: $UNIQUE_BUCKET"
echo ""

# Create S3 bucket for static website
echo "Creating S3 bucket for frontend..."
if ! aws s3 ls "s3://$UNIQUE_BUCKET" 2>&1 | grep -v 'NoSuchBucket'; then
    aws s3api create-bucket \
        --bucket "$UNIQUE_BUCKET" \
        --region "$REGION" 2>&1 || true

    echo "✓ S3 bucket created: $UNIQUE_BUCKET"
else
    echo "✓ S3 bucket already exists: $UNIQUE_BUCKET"
fi

# Enable static website hosting
echo ""
echo "Enabling static website hosting..."
aws s3 website "s3://$UNIQUE_BUCKET" \
    --index-document index.html \
    --error-document index.html

# Set bucket policy for public read access
echo ""
echo "Setting bucket policy for public access..."
cat > /tmp/bucket-policy.json <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "PublicReadGetObject",
            "Effect": "Allow",
            "Principal": "*",
            "Action": "s3:GetObject",
            "Resource": "arn:aws:s3:::${UNIQUE_BUCKET}/*"
        }
    ]
}
EOF

aws s3api put-bucket-policy \
    --bucket "$UNIQUE_BUCKET" \
    --policy file:///tmp/bucket-policy.json

# Disable block public access
aws s3api put-public-access-block \
    --bucket "$UNIQUE_BUCKET" \
    --public-access-block-configuration \
        "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"

rm /tmp/bucket-policy.json

# Update frontend URLs to point to your services
# You'll need to replace these with your actual service URLs
echo ""
echo "Note: You need to update frontend/app.js with your backend URLs"
echo "Current configuration uses localhost - update before deploying to production"

# Upload frontend files
echo ""
echo "Uploading frontend files..."
aws s3 sync frontend/ "s3://$UNIQUE_BUCKET" \
    --exclude ".*" \
    --cache-control "max-age=3600"

# Set content types
aws s3 cp "s3://$UNIQUE_BUCKET/index.html" "s3://$UNIQUE_BUCKET/index.html" \
    --content-type "text/html" \
    --metadata-directive REPLACE

aws s3 cp "s3://$UNIQUE_BUCKET/app.js" "s3://$UNIQUE_BUCKET/app.js" \
    --content-type "application/javascript" \
    --metadata-directive REPLACE

aws s3 cp "s3://$UNIQUE_BUCKET/style.css" "s3://$UNIQUE_BUCKET/style.css" \
    --content-type "text/css" \
    --metadata-directive REPLACE

echo "✓ Frontend files uploaded"

# Get website URL
WEBSITE_URL="http://${UNIQUE_BUCKET}.s3-website-${REGION}.amazonaws.com"

echo ""
echo "========================================="
echo "✓ Frontend Deployment Complete!"
echo "========================================="
echo ""
echo "Your frontend URL:"
echo "  $WEBSITE_URL"
echo ""
echo "IMPORTANT: To make this work, you need to:"
echo "  1. Deploy your backend services to AWS (EC2/ECS/Lambda)"
echo "  2. Update frontend/app.js with backend service URLs"
echo "  3. Re-run this script to upload updated files"
echo ""
echo "Next steps for full deployment:"
echo "  - Deploy services to AWS (see deploy-services.sh)"
echo "  - Update CORS settings on backend services"
echo "  - Consider using CloudFront for HTTPS"
echo ""
