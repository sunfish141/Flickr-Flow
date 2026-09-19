# Public export privacy review

Scope: this export directory only, including all eight CSVs, every metadata
file, the export script, README, manifest, and checksums. The surrounding
repository, its history, raw data archive, and any previously uploaded copies
are outside this review.

## Finding and correction

The original export contained 21 absolute workspace path references in five
copied source manifests. Those references exposed a local account name and
workspace location. They have been replaced with repository-relative paths in:

- `metadata/weather_features_source_manifest.json` (2 references)
- `metadata/weather_history_source_manifest.json` (1 reference)
- `metadata/directional_features_source_manifest.json` (1 reference)
- `metadata/vegetation_features_source_manifest.json` (2 references)
- `metadata/landscape_features_source_manifest.json` (15 references)

The exporter now performs the same sanitization for future exports. The main
manifest distinguishes the packaged manifest checksum from the original source
manifest checksum. Training CSV content is unchanged.

## Checks performed

- Scanned the entire contents of every CSV and supporting file, including
  ignored and hidden files; the scan was not limited to sample rows or tracked
  files.
- Checked for local account names and home-directory paths, email addresses,
  private-key blocks, common service-token formats, credential assignments,
  network addresses, sensitive personal-field names, and URLs that could carry
  credentials or private endpoints.
- Reviewed all CSV schemas and nested provenance metadata. Coordinates are
  wildfire candidate-cell locations; incident identifiers, satellite evidence
  identifiers and checksums describe scientific source data. The export has
  no contact, customer, patient, personnel, or account-record tables.
- Checked the directory inventory for extra files and symbolic links. No
  environment files, credential files, source archives, or symlinks are bundled.
- Rechecked CSV hashes against the original export manifest, packaged metadata
  hashes against the updated manifest, and the complete folder checksum list.

After sanitization, the content scan found no remaining matches for the checked
credential, contact-information, or local-identity patterns. This is a bounded
content and schema review, not a guarantee against every possible form of
sensitive information. The check does not establish redistribution rights or
audit anything outside this folder.

Publish the sanitized folder with its updated `SHA256SUMS`, not an earlier copy.
