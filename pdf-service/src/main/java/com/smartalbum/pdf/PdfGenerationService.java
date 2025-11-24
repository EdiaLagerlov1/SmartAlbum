package com.smartalbum.pdf;

import com.smartalbum.common.*;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDPage;
import org.apache.pdfbox.pdmodel.PDPageContentStream;
import org.apache.pdfbox.pdmodel.common.PDRectangle;
import org.apache.pdfbox.pdmodel.graphics.image.PDImageXObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.List;

@Service
public class PdfGenerationService {
    private static final Logger logger = LoggerFactory.getLogger(PdfGenerationService.class);

    @Autowired
    private S3Service s3Service;

    @Autowired
    private DynamoDBService dynamoDBService;

    public void generatePdf(String albumId) {
        logger.info("Starting PDF generation: albumId={}", albumId);

        try {
            Album album = dynamoDBService.getAlbum(albumId);
            if (album == null) {
                throw new IllegalArgumentException("Album not found: " + albumId);
            }

            dynamoDBService.updateAlbumStatus(albumId, Album.AlbumStatus.PROCESSING);

            List<String> imageKeys = album.getImageKeys();
            logger.info("Processing {} images for albumId={}", imageKeys.size(), albumId);

            byte[] pdfBytes = createPdfWithImages(imageKeys, album.getAlbumName());

            String pdfKey = String.format("albums/%s/output/album.pdf", albumId);
            s3Service.uploadBytes(pdfKey, pdfBytes, "application/pdf");

            album.setPdfKey(pdfKey);
            album.setStatus(Album.AlbumStatus.READY);
            dynamoDBService.saveAlbum(album);

            logger.info("PDF generation completed successfully: albumId={}, pdfKey={}", albumId, pdfKey);

        } catch (Exception e) {
            logger.error("PDF generation failed for albumId={}", albumId, e);
            try {
                dynamoDBService.updateAlbumStatus(albumId, Album.AlbumStatus.FAILED);
            } catch (Exception ex) {
                logger.error("Failed to update album status to FAILED", ex);
            }
            throw new RuntimeException("Failed to generate PDF", e);
        }
    }

    private byte[] createPdfWithImages(List<String> imageKeys, String albumName) throws Exception {
        logger.info("Creating PDF with {} images", imageKeys.size());

        try (PDDocument document = new PDDocument();
             ByteArrayOutputStream outputStream = new ByteArrayOutputStream()) {

            for (int i = 0; i < imageKeys.size(); i += 2) {
                PDPage page = new PDPage(PDRectangle.A4);
                document.addPage(page);

                try (PDPageContentStream contentStream =
                             new PDPageContentStream(document, page)) {

                    float pageWidth = page.getMediaBox().getWidth();
                    float pageHeight = page.getMediaBox().getHeight();
                    float margin = 50;
                    float imageWidth = pageWidth - (2 * margin);
                    float imageHeight = (pageHeight - (3 * margin)) / 2;

                    addImageToPage(document, contentStream, imageKeys.get(i),
                            margin, pageHeight - margin - imageHeight, imageWidth, imageHeight);

                    if (i + 1 < imageKeys.size()) {
                        addImageToPage(document, contentStream, imageKeys.get(i + 1),
                                margin, margin, imageWidth, imageHeight);
                    }
                }

                logger.info("Added page {} with images", (i / 2) + 1);
            }

            document.save(outputStream);
            logger.info("PDF created successfully with {} pages", document.getNumberOfPages());
            return outputStream.toByteArray();
        }
    }

    private void addImageToPage(PDDocument document, PDPageContentStream contentStream,
                                 String imageKey, float x, float y, float width, float height)
            throws Exception {

        logger.info("Adding image to page: key={}", imageKey);

        try (InputStream imageStream = s3Service.downloadFile(imageKey)) {
            PDImageXObject image = PDImageXObject.createFromByteArray(
                    document, imageStream.readAllBytes(), imageKey);

            float aspectRatio = (float) image.getWidth() / image.getHeight();
            float targetAspect = width / height;

            float drawWidth, drawHeight, drawX, drawY;

            if (aspectRatio > targetAspect) {
                drawWidth = width;
                drawHeight = width / aspectRatio;
                drawX = x;
                drawY = y + (height - drawHeight) / 2;
            } else {
                drawHeight = height;
                drawWidth = height * aspectRatio;
                drawX = x + (width - drawWidth) / 2;
                drawY = y;
            }

            contentStream.drawImage(image, drawX, drawY, drawWidth, drawHeight);
            logger.info("Image added successfully: key={}", imageKey);
        }
    }
}
