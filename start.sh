#!/bin/bash

echo "========================================="
echo "Starting SmartAlbum Application"
echo "========================================="

# Check if AWS resources exist
echo ""
echo "Checking AWS resources..."
BUCKET_EXISTS=$(aws s3 ls s3://smartalbum-photos-bucket 2>&1 | grep -v "NoSuchBucket" | wc -l)
TABLE_EXISTS=$(aws dynamodb describe-table --table-name SmartAlbum-Albums --region us-east-1 2>&1 | grep -v "ResourceNotFoundException" | wc -l)

if [ "$BUCKET_EXISTS" -eq 0 ] || [ "$TABLE_EXISTS" -eq 0 ]; then
    echo ""
    echo "AWS resources not found. Setting up infrastructure..."
    ./scripts/setup-aws.sh
fi

echo ""
echo "Starting Docker services..."
docker-compose up --build

echo ""
echo "Application stopped."
