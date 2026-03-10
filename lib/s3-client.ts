import { S3Client, GetObjectCommand, DeleteObjectCommand, PutObjectCommand } from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';

const PRESIGNED_URL_EXPIRES_IN = 900; // 15 minutes

function getS3Config() {
  const bucket = process.env.S3_BUCKET_NAME;
  const accessKeyId = process.env.S3_ACCESS_KEY_ID;
  const secretAccessKey = process.env.S3_SECRET_ACCESS_KEY;
  const region = process.env.S3_REGION || 'ru-central1';
  const endpoint = process.env.S3_ENDPOINT || 'https://storage.yandexcloud.net';

  if (!bucket || !accessKeyId || !secretAccessKey) {
    throw new Error('Missing S3 configuration: S3_BUCKET_NAME, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY are required');
  }

  return { bucket, accessKeyId, secretAccessKey, region, endpoint };
}

let s3Client: S3Client | null = null;

function getS3Client(): S3Client {
  if (!s3Client) {
    const config = getS3Config();
    s3Client = new S3Client({
      region: config.region,
      endpoint: config.endpoint,
      credentials: {
        accessKeyId: config.accessKeyId,
        secretAccessKey: config.secretAccessKey,
      },
      forcePathStyle: true,
    });
  }
  return s3Client;
}

export function getBucketName(): string {
  return getS3Config().bucket;
}

export async function generatePresignedUploadUrl(
  objectKey: string,
  contentType: string,
): Promise<string> {
  const client = getS3Client();
  const command = new PutObjectCommand({
    Bucket: getBucketName(),
    Key: objectKey,
    ContentType: contentType,
  });

  return getSignedUrl(client, command, { expiresIn: PRESIGNED_URL_EXPIRES_IN });
}

export async function downloadFileFromS3(objectKey: string): Promise<Buffer> {
  const client = getS3Client();
  const command = new GetObjectCommand({
    Bucket: getBucketName(),
    Key: objectKey,
  });

  const response = await client.send(command);

  if (!response.Body) {
    throw new Error(`Empty response body for object: ${objectKey}`);
  }

  const chunks: Uint8Array[] = [];
  const stream = response.Body as AsyncIterable<Uint8Array>;
  for await (const chunk of stream) {
    chunks.push(chunk);
  }

  return Buffer.concat(chunks);
}

export async function deleteFileFromS3(objectKey: string): Promise<void> {
  const client = getS3Client();
  const command = new DeleteObjectCommand({
    Bucket: getBucketName(),
    Key: objectKey,
  });

  await client.send(command);
}
