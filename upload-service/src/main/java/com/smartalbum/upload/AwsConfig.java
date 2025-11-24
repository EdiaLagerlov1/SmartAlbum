package com.smartalbum.upload;

import com.smartalbum.common.Config;
import com.smartalbum.common.DynamoDBService;
import com.smartalbum.common.S3Service;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;

@Configuration
public class AwsConfig {

    @Bean
    public S3Client s3Client() {
        return S3Client.builder()
                .region(Region.of(Config.REGION))
                .build();
    }

    @Bean
    public S3Presigner s3Presigner() {
        return S3Presigner.builder()
                .region(Region.of(Config.REGION))
                .build();
    }

    @Bean
    public DynamoDbClient dynamoDbClient() {
        return DynamoDbClient.builder()
                .region(Region.of(Config.REGION))
                .build();
    }

    @Bean
    public S3Service s3Service(S3Client s3Client, S3Presigner s3Presigner) {
        return new S3Service(s3Client, s3Presigner, Config.BUCKET_NAME);
    }

    @Bean
    public DynamoDBService dynamoDBService(DynamoDbClient dynamoDbClient) {
        return new DynamoDBService(dynamoDbClient, Config.TABLE_NAME);
    }
}
