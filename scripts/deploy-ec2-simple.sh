#!/bin/bash

set -e

echo "========================================="
echo "SmartAlbum EC2 Deployment Guide (FREE)"
echo "========================================="
echo ""
echo "This will deploy all 3 services on ONE EC2 instance (Free Tier)"
echo ""
echo "STEP 1: Create EC2 Instance"
echo "----------------------------"
echo "1. Go to AWS Console > EC2"
echo "2. Click 'Launch Instance'"
echo "3. Choose:"
echo "   - Name: SmartAlbum"
echo "   - AMI: Amazon Linux 2023"
echo "   - Instance Type: t2.micro (Free tier)"
echo "   - Key pair: Create new or use existing"
echo "   - Security Group: Allow ports 22, 8080, 8082, 8083, 80"
echo "4. Launch instance"
echo ""
echo "STEP 2: After instance is running, copy its Public IP"
echo ""
read -p "Enter your EC2 Public IP: " EC2_IP

if [ -z "$EC2_IP" ]; then
    echo "No IP provided. Exiting."
    exit 1
fi

echo ""
echo "STEP 3: Build application"
cd /Users/edialagerlov/SmartAlbum
mvn clean package -DskipTests

echo ""
echo "STEP 4: Package for deployment"
mkdir -p deploy
cp upload-service/target/upload-service-1.0.0.jar deploy/
cp pdf-service/target/pdf-service-1.0.0.jar deploy/
cp download-service/target/download-service-1.0.0.jar deploy/

# Create startup script
cat > deploy/start-services.sh <<'EOFSCRIPT'
#!/bin/bash
cd /home/ec2-user/smartalbum

# Install Java if needed
if ! command -v java &> /dev/null; then
    sudo yum install -y java-17-amazon-corretto
fi

# Start services
nohup java -jar upload-service-1.0.0.jar > upload.log 2>&1 &
nohup java -jar pdf-service-1.0.0.jar > pdf.log 2>&1 &
nohup java -jar download-service-1.0.0.jar > download.log 2>&1 &

echo "Services started!"
echo "Upload: http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):8080"
echo "PDF: http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):8082"
echo "Download: http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):8083"
EOFSCRIPT

chmod +x deploy/start-services.sh

echo "✓ Deployment package ready"
echo ""
echo "STEP 5: Copy files to EC2 (requires SSH key)"
echo "-------------------------------------------"
read -p "Enter path to your .pem key file: " KEY_FILE

if [ ! -f "$KEY_FILE" ]; then
    echo "Key file not found: $KEY_FILE"
    exit 1
fi

echo "Copying files to EC2..."
ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no ec2-user@${EC2_IP} "mkdir -p smartalbum"
scp -i "$KEY_FILE" deploy/*.jar ec2-user@${EC2_IP}:~/smartalbum/
scp -i "$KEY_FILE" deploy/start-services.sh ec2-user@${EC2_IP}:~/smartalbum/

echo ""
echo "STEP 6: Start services on EC2"
ssh -i "$KEY_FILE" ec2-user@${EC2_IP} "cd smartalbum && ./start-services.sh"

echo ""
echo "========================================="
echo "✓ Deployment Complete!"
echo "========================================="
echo ""
echo "Your service URLs:"
echo "  Upload:   http://${EC2_IP}:8080"
echo "  PDF:      http://${EC2_IP}:8082"
echo "  Download: http://${EC2_IP}:8083"
echo ""
echo "NEXT: Update frontend and deploy to S3"
echo ""
echo "Run: ./scripts/deploy-frontend-to-s3.sh ${EC2_IP}"
echo ""
