package com.smartalbum.upload;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import java.util.HashMap;
import java.util.Map;

@Component
public class PdfServiceClient {
    private static final Logger logger = LoggerFactory.getLogger(PdfServiceClient.class);

    @Value("${pdf.service.url:http://localhost:8082}")
    private String pdfServiceUrl;

    private final RestTemplate restTemplate = new RestTemplate();

    public void triggerPdfGeneration(String albumId) {
        logger.info("Triggering PDF generation: albumId={}, url={}", albumId, pdfServiceUrl);

        try {
            Map<String, String> request = new HashMap<>();
            request.put("albumId", albumId);

            String url = pdfServiceUrl + "/api/pdf/generate";
            restTemplate.postForObject(url, request, String.class);

            logger.info("Successfully triggered PDF generation for albumId={}", albumId);
        } catch (Exception e) {
            logger.error("Failed to trigger PDF generation for albumId={}", albumId, e);
        }
    }
}
