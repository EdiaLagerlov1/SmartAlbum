#!/bin/bash
# Local Testing Script for SmartAlbum

echo "========================================="
echo "SmartAlbum - Local Testing"
echo "========================================="
echo ""

# Check if services are already running
if pgrep -f "upload-service.*spring-boot:run" > /dev/null; then
    echo "⚠️  Services are already running. Stopping them first..."
    pkill -f "spring-boot:run"
    sleep 3
fi

# Build all services
echo "📦 Building all services..."
mvn clean package -DskipTests
if [ $? -ne 0 ]; then
    echo "❌ Build failed!"
    exit 1
fi
echo "✅ Build successful!"
echo ""

# Start Upload Service
echo "🚀 Starting Upload Service (port 8080)..."
cd upload-service
mvn spring-boot:run > /tmp/smartalbum-upload.log 2>&1 &
UPLOAD_PID=$!
cd ..
sleep 5

# Start PDF Service
echo "🚀 Starting PDF Service (port 8082)..."
cd pdf-service
mvn spring-boot:run > /tmp/smartalbum-pdf.log 2>&1 &
PDF_PID=$!
cd ..
sleep 5

# Start Download Service
echo "🚀 Starting Download Service (port 8083)..."
cd download-service
mvn spring-boot:run > /tmp/smartalbum-download.log 2>&1 &
DOWNLOAD_PID=$!
cd ..
sleep 5

# Check if services are running
echo ""
echo "🔍 Checking services..."
if curl -s http://localhost:8080/actuator/health > /dev/null 2>&1; then
    echo "✅ Upload Service: Running"
else
    echo "❌ Upload Service: Not responding"
fi

if curl -s http://localhost:8082/actuator/health > /dev/null 2>&1; then
    echo "✅ PDF Service: Running"
else
    echo "❌ PDF Service: Not responding"
fi

if curl -s http://localhost:8083/actuator/health > /dev/null 2>&1; then
    echo "✅ Download Service: Running"
else
    echo "❌ Download Service: Not responding"
fi

# Start frontend
echo ""
echo "🌐 Starting Frontend (port 8000)..."
cd frontend
python3 -m http.server 8000 > /dev/null 2>&1 &
FRONTEND_PID=$!
cd ..
sleep 2

echo ""
echo "========================================="
echo "✅ All services started!"
echo "========================================="
echo ""
echo "📱 Frontend: http://localhost:8000"
echo "🔧 Upload Service: http://localhost:8080"
echo "🔧 PDF Service: http://localhost:8082"
echo "🔧 Download Service: http://localhost:8083"
echo ""
echo "📋 View logs:"
echo "  tail -f /tmp/smartalbum-upload.log"
echo "  tail -f /tmp/smartalbum-pdf.log"
echo "  tail -f /tmp/smartalbum-download.log"
echo ""
echo "🛑 Stop all services:"
echo "  pkill -f 'spring-boot:run'"
echo "  pkill -f 'python3 -m http.server 8000'"
echo ""
