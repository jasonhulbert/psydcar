# Psydcar sync service

The app uses an HTTPS control endpoint for enrollment, device pairing, and version checks. The endpoint signs individual S3 transfers. Devices keep working files and search indexes locally. The bucket stores device records under `sync/<account>/devices.json` and a manifest and immutable file contents per sidecar under `sync/<account>/sidecars/<sidecar>/`.

A device credential determines the account prefix on every request. The service uses compare-and-swap for device records and manifests. Upload URLs bind the file size, SHA-256 checksum, and create-only condition. The service verifies new contents before publishing a manifest. Revocation stops new requests from the revoked device. Previously issued transfer URLs expire within two minutes.

## Package and deploy to development

Build the zip from the repository root with Python and pip. The archive includes the pinned AWS SDK. The desktop application does not require it.

```sh
sh cloud/sync/package.sh
```

The template uses the existing bucket and execution role as parameters. It creates the Lambda and a throttled HTTPS API route. It leaves the bucket and role under their existing ownership. For the supplied development environment, first verify the active AWS account.

```sh
aws sts get-caller-identity
```

Use account `619561322191` and region `us-east-1`. Generate a high-entropy enrollment key and store it privately. Pass only its SHA-256 digest to CloudFormation. The following command prompts for the key without putting it in shell history.

```sh
enrollment_digest=$(python3 -c 'from getpass import getpass; from hashlib import sha256; print(sha256(getpass("Enrollment key: ").encode()).hexdigest())')
bucket=psydcar-sync-dev-619561322191-us-east-1-an
artifact="deployments/sync/$(shasum -a 256 build/psydcar-sync.zip | cut -d ' ' -f 1).zip"

aws s3 cp build/psydcar-sync.zip "s3://$bucket/$artifact" --region us-east-1
aws cloudformation deploy --region us-east-1 --stack-name psydcar-sync-dev \
  --template-file cloud/sync/template.yaml \
  --parameter-overrides "SyncBucket=$bucket" "ArtifactKey=$artifact" \
  "SyncRoleArn=arn:aws:iam::619561322191:role/psydcar-sync-dev" "EnrollmentDigest=$enrollment_digest"
aws cloudformation describe-stacks --region us-east-1 --stack-name psydcar-sync-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`SyncUrl`].OutputValue' --output text
```

Set `PSYDCAR_SYNC_URL` to the output on each development device before starting `psydcar app`. The first device uses the enrollment key. The second uses a pairing code generated in the first device’s workspace. Cloud deployment requires a principal with permission to upload the artifact, deploy the stack, and pass the execution role.

## Release operations

Development enrollment is invite-only. The bucket retains old manifest versions and immutable contents, including uploads that were interrupted before publication. Establish retention and account cleanup before opening enrollment to customers. Cleanup needs to preserve blobs referenced by current manifests and the recovery window. Use request, transfer, and storage usage to set a budget for the hosted service.
