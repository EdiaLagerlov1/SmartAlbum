#!/bin/bash

set -e

echo "========================================="
echo "SmartAlbum AWS Infrastructure Cleanup"
echo "========================================="

REGION="us-east-1"
BUCKET_NAME="smartalbum-photos-bucket"
TABLE_NAME="SmartAlbum-Albums"

echo ""
echo "WARNING: This will delete all SmartAlbum AWS resources!"
echo "  - S3 Bucket: $BUCKET_NAME (including all files)"
echo "  - DynamoDB Table: $TABLE_NAME (including all data)"
echo ""
read -p "Are you sure you want to continue? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Cleanup cancelled."
    exit 0
fi

# Delete S3 bucket
echo ""
echo "Deleting S3 bucket: $BUCKET_NAME..."
if aws s3 ls "s3://$BUCKET_NAME" 2>&1 | grep -v 'NoSuchBucket'; then
    echo "Emptying bucket..."
    aws s3 rm "s3://$BUCKET_NAME" --recursive

    echo "Deleting bucket..."
    aws s3api delete-bucket --bucket "$BUCKET_NAME" --region "$REGION"
    echo "✓ S3 bucket deleted: $BUCKET_NAME"
else
    echo "✓ S3 bucket does not exist: $BUCKET_NAME"
fi

# Delete DynamoDB table
echo ""
echo "Deleting DynamoDB table: $TABLE_NAME..."
if aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$REGION" &> /dev/null; then
    aws dynamodb delete-table --table-name "$TABLE_NAME" --region "$REGION"

    echo "Waiting for table deletion..."
    aws dynamodb wait table-not-exists --table-name "$TABLE_NAME" --region "$REGION"
    echo "✓ DynamoDB table deleted: $TABLE_NAME"
else
    echo "✓ DynamoDB table does not exist: $TABLE_NAME"
fi

echo ""
echo "========================================="
echo "✓ AWS Infrastructure Cleanup Complete!"
echo "========================================="
