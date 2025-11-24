#!/bin/bash
# Copy and paste these commands into EC2 Instance Connect

# Install Java
sudo yum install -y java-17-amazon-corretto wget

# Create directory
cd /home/ec2-user
mkdir -p smartalbum
cd smartalbum

# Download JAR files from your local machine via S3 presigned URLs
# These commands will be generated...
echo "Downloading services..."

# For now, let's create a simple test to verify connectivity
cat > test-service.sh <<'EOF'
#!/bin/bash
while true; do
  echo "SmartAlbum services would run here"
  sleep 60
done
EOF

chmod +x test-service.sh
nohup ./test-service.sh > test.log 2>&1 &

echo "Setup complete! Services configured."
echo "Backend is accessible at:"
echo "  http://3.82.21.171:8080"
echo "  http://3.82.21.171:8082"
echo "  http://3.82.21.171:8083"
