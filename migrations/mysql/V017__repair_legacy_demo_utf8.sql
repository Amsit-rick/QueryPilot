-- Repair only the deterministic V001 demo rows that an older Windows setup
-- stored after interpreting UTF-8 source bytes as Windows-1252.  The primary
-- key and exact corrupted byte sequence must both match, so tenant or imported
-- business data is never guessed or bulk-transcoded.
UPDATE sales
SET region = '华东'
WHERE id IN (1, 3)
  AND HEX(region) = 'C3A5C28DC5BDC3A4C2B8C593';

UPDATE sales
SET region = '华南'
WHERE id = 2
  AND HEX(region) = 'C3A5C28DC5BDC3A5C28DE28094';

UPDATE customers
SET name = '星河科技'
WHERE id = 1
  AND HEX(name) = 'C3A6CB9CC5B8C3A6C2B2C2B3C3A7C2A7E28098C3A6C5A0E282AC';

UPDATE customers
SET region = '华东'
WHERE id = 1
  AND HEX(region) = 'C3A5C28DC5BDC3A4C2B8C593';

UPDATE customers
SET name = '南方零售'
WHERE id = 2
  AND HEX(name) = 'C3A5C28DE28094C3A6E28093C2B9C3A9E280BAC2B6C3A5E2809DC2AE';

UPDATE customers
SET region = '华南'
WHERE id = 2
  AND HEX(region) = 'C3A5C28DC5BDC3A5C28DE28094';
