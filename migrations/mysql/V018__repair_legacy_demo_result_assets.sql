-- Preserve task-history snapshots while repairing the one deterministic result
-- shape produced from the legacy V001 sales demo rows.  Every structural field
-- and both exact corrupted byte sequences must match before JSON is changed.
UPDATE data_task_artifacts
SET metadata_json = JSON_SET(
    metadata_json,
    '$.rows[0][0]', '华东',
    '$.rows[1][0]', '华南',
    '$.byte_size', 33
)
WHERE kind = 'table'
  AND JSON_LENGTH(JSON_EXTRACT(metadata_json, '$.columns')) = 2
  AND JSON_EXTRACT(metadata_json, '$.columns[0]') = 'region'
  AND JSON_EXTRACT(metadata_json, '$.columns[1]') = 'total_amount'
  AND JSON_LENGTH(JSON_EXTRACT(metadata_json, '$.rows')) = 2
  AND HEX(JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.rows[0][0]'))) = 'C3A5C28DC5BDC3A4C2B8C593'
  AND HEX(JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.rows[1][0]'))) = 'C3A5C28DC5BDC3A5C28DE28094';
