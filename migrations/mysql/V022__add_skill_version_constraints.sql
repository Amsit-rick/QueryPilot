ALTER TABLE skill_versions
  ADD CONSTRAINT chk_skill_version_state
    CHECK (state IN ('draft','canary','active','retired')),
  ADD CONSTRAINT chk_skill_canary_percent
    CHECK (canary_percent BETWEEN 0 AND 100);
