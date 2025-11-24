#!/bin/bash

set -e

echo "========================================="
echo "SmartAlbum Lambda Deployment (FREE Tier)"
echo "========================================="

REGION="us-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo ""
echo "Configuration:"
echo "  Account ID: $ACCOUNT_ID"
echo "  Region: $REGION"
echo ""

# Create Lambda execution role
echo "Creating Lambda execution role..."
ROLE_NAME="SmartAlbumLambdaRole"

# Check if role exists
if ! aws iam get-role --role-name "$ROLE_NAME" 2>/dev/null; then
    cat > /tmp/trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

    aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document file:///tmp/trust-policy.json

    # Attach policies
    aws iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

    aws iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn "arn:aws:iam::aws:policy/AmazonS3FullAccess"

    aws iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess"

    rm /tmp/trust-policy.json

    echo "Waiting for role to be ready..."
    sleep 10

    echo "✓ Lambda role created: $ROLE_NAME"
else
    echo "✓ Lambda role already exists: $ROLE_NAME"
fi

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

echo ""
echo "Building Lambda deployment packages..."
cd /Users/edialagerlov/SmartAlbum

# Build all services
mvn clean package -DskipTests

# Create Lambda deployment packages
mkdir -p lambda-deploy

# Upload Service Lambda
echo "Creating Upload Service Lambda..."
cd upload-service/target
unzip -q upload-service-1.0.0.jar -d upload-lambda
cd upload-lambda
zip -qr ../../../lambda-deploy/upload-service.zip .
cd ../../..

# PDF Service Lambda
echo "Creating PDF Service Lambda..."
cd pdf-service/target
unzip -q pdf-service-1.0.0.jar -d pdf-lambda
cd pdf-lambda
zip -qr ../../../lambda-deploy/pdf-service.zip .
cd ../../..

# Download Service Lambda
echo "Creating Download Service Lambda..."
cd download-service/target
unzip -q download-service-1.0.0.jar -d download-lambda
cd download-lambda
zip -qr ../../../lambda-deploy/download-service.zip .
cd ../../..

echo "✓ Lambda packages created"

# Deploy Lambda functions
echo ""
echo "Deploying Lambda functions..."

# Upload Service Lambda
if aws lambda get-function --function-name SmartAlbum-Upload 2>/dev/null; then
    echo "Updating Upload Service Lambda..."
    aws lambda update-function-code \
        --function-name SmartAlbum-Upload \
        --zip-file fileb://lambda-deploy/upload-service.zip \
        --region "$REGION" > /dev/null
else
    echo "Creating Upload Service Lambda..."
    aws lambda create-function \
        --function-name SmartAlbum-Upload \
        --runtime java17 \
        --role "$ROLE_ARN" \
        --handler org.springframework.cloud.function.adapter.aws.FunctionInvoker \
        --zip-file fileb://lambda-deploy/upload-service.zip \
        --timeout 60 \
        --memory-size 512 \
        --region "$REGION" > /dev/null
fi

# PDF Service Lambda
if aws lambda get-function --function-name SmartAlbum-PDF 2>/dev/null; then
    echo "Updating PDF Service Lambda..."
    aws lambda update-function-code \
        --function-name SmartAlbum-PDF \
        --zip-file fileb://lambda-deploy/pdf-service.zip \
        --region "$REGION" > /dev/null
else
    echo "Creating PDF Service Lambda..."
    aws lambda create-function \
        --function-name SmartAlbum-PDF \
        --runtime java17 \
        --role "$ROLE_ARN" \
        --handler org.springframework.cloud.function.adapter.aws.FunctionInvoker \
        --zip-file fileb://lambda-deploy/pdf-service.zip \
        --timeout 300 \
        --memory-size 1024 \
        --region "$REGION" > /dev/null
fi

# Download Service Lambda
if aws lambda get-function --function-name SmartAlbum-Download 2>/dev/null; then
    echo "Updating Download Service Lambda..."
    aws lambda update-function-code \
        --function-name SmartAlbum-Download \
        --zip-file fileb://lambda-deploy/download-service.zip \
        --region "$REGION" > /dev/null
else
    echo "Creating Download Service Lambda..."
    aws lambda create-function \
        --function-name SmartAlbum-Download \
        --runtime java17 \
        --role "$ROLE_ARN" \
        --handler org.springframework.cloud.function.adapter.aws.FunctionInvoker \
        --zip-file fileb://lambda-deploy/download-service.zip \
        --timeout 30 \
        --memory-size 512 \
        --region "$REGION" > /dev/null
fi

echo "✓ Lambda functions deployed"

echo ""
echo "========================================="
echo "⚠️  MANUAL STEPS REQUIRED"
echo "========================================="
echo ""
echo "Lambda functions are created but need API Gateway setup."
echo "Spring Boot apps don't directly work with Lambda without modification."
echo ""
echo "RECOMMENDED ALTERNATIVE:"
echo "Use AWS Elastic Beanstalk (FREE tier available)"
echo "  - Easier for Spring Boot apps"
echo "  - 750 hours/month EC2 free tier"
echo "  - Better fit for your microservices"
echo ""
echo "Would you like me to create an Elastic Beanstalk deployment instead?"
echo ""
