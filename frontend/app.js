// Use localhost for local testing, EC2 for production
const IS_LOCAL = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
const UPLOAD_SERVICE_URL = IS_LOCAL ? 'http://localhost:8080' : 'http://3.82.21.171:8080';
const DOWNLOAD_SERVICE_URL = IS_LOCAL ? 'http://localhost:8083' : 'http://3.82.21.171:8083';

const uploadForm = document.getElementById('uploadForm');
const submitBtn = document.getElementById('submitBtn');
const statusMessage = document.getElementById('statusMessage');
const progressContainer = document.getElementById('progressContainer');
const progressBar = document.getElementById('progressBar');
const progressText = document.getElementById('progressText');
const downloadSection = document.getElementById('downloadSection');
const downloadLink = document.getElementById('downloadLink');

uploadForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const albumName = document.getElementById('albumName').value;
    const imagesInput = document.getElementById('images');
    const images = imagesInput.files;

    if (images.length === 0) {
        showMessage('Please select at least one image', 'error');
        return;
    }

    if (images.length > 50) {
        showMessage('Maximum 50 images allowed', 'error');
        return;
    }

    for (let image of images) {
        if (image.size > 5 * 1024 * 1024) {
            showMessage(`Image ${image.name} exceeds 5MB limit`, 'error');
            return;
        }
    }

    hideMessage();
    hideDownloadSection();
    showProgress();
    disableForm();

    try {
        const formData = new FormData();
        formData.append('albumName', albumName);

        for (let image of images) {
            formData.append('images', image);
        }

        updateProgress(30, 'Uploading images...');

        const uploadResponse = await fetch(`${UPLOAD_SERVICE_URL}/api/albums/upload`, {
            method: 'POST',
            body: formData
        });

        if (!uploadResponse.ok) {
            const error = await uploadResponse.json();
            throw new Error(error.error || 'Upload failed');
        }

        const uploadResult = await uploadResponse.json();
        const albumId = uploadResult.albumId;

        updateProgress(60, 'Generating PDF...');

        await pollAlbumStatus(albumId);

        updateProgress(90, 'Preparing download...');

        const downloadResponse = await fetch(
            `${DOWNLOAD_SERVICE_URL}/api/albums/${albumId}/download`
        );

        if (!downloadResponse.ok) {
            const error = await downloadResponse.json();
            throw new Error(error.error || 'Failed to get download link');
        }

        const downloadResult = await downloadResponse.json();

        updateProgress(100, 'Complete!');
        hideProgress();
        showDownload(downloadResult.downloadUrl);
        showMessage('Album created successfully!', 'success');

    } catch (error) {
        hideProgress();
        showMessage('Error: ' + error.message, 'error');
        console.error('Error:', error);
    } finally {
        enableForm();
    }
});

async function pollAlbumStatus(albumId, maxAttempts = 30) {
    for (let i = 0; i < maxAttempts; i++) {
        await sleep(2000);

        const statusResponse = await fetch(
            `${UPLOAD_SERVICE_URL}/api/albums/status/${albumId}`
        );

        if (!statusResponse.ok) {
            throw new Error('Failed to check album status');
        }

        const statusResult = await statusResponse.json();

        if (statusResult.status === 'READY') {
            return;
        }

        if (statusResult.status === 'FAILED') {
            throw new Error('PDF generation failed');
        }
    }

    throw new Error('PDF generation timeout');
}

function showProgress() {
    progressContainer.classList.remove('hidden');
}

function hideProgress() {
    progressContainer.classList.add('hidden');
}

function updateProgress(percent, text) {
    progressBar.style.width = percent + '%';
    progressText.textContent = text;
}

function showMessage(message, type) {
    statusMessage.textContent = message;
    statusMessage.className = `status-message ${type}`;
    statusMessage.classList.remove('hidden');
}

function hideMessage() {
    statusMessage.classList.add('hidden');
}

function showDownload(url) {
    downloadLink.href = url;
    downloadSection.classList.remove('hidden');
}

function hideDownloadSection() {
    downloadSection.classList.add('hidden');
}

function disableForm() {
    submitBtn.disabled = true;
    submitBtn.textContent = 'Processing...';
}

function enableForm() {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Generate Album';
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}
