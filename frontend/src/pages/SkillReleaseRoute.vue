<script setup>
import {inject} from 'vue'
import {appContextKey,skillEvaluationKey} from '../appContext'
import SkillReleasePage from '../components/SkillReleasePage.vue'
const {skillCatalog,skillVersions,skillReleaseDraft,skillCanaryPercent,skillPreviewSubject,skillPreviewResult,skillReleaseError,skillReleaseLoading,skillReleaseBusyId,loadSkillReleases,selectSkill,createSkillDraft,startSkillCanary,publishSkillVersion,rollbackSkillVersion,previewSkillResolution,openView}=inject(appContextKey)
const {skillEvaluationResult,skillQueryQualityResult,evaluateSkillDraft,evaluateSkillQueryQuality}=inject(skillEvaluationKey)
</script>
<template><SkillReleasePage :catalog="skillCatalog" :versions="skillVersions" :draft="skillReleaseDraft" :canary-percent="skillCanaryPercent" :preview-subject="skillPreviewSubject" :preview-result="skillPreviewResult" :evaluation-result="skillEvaluationResult" :query-quality-result="skillQueryQualityResult" :error="skillReleaseError" :loading="skillReleaseLoading" :busy-id="skillReleaseBusyId" @refresh="loadSkillReleases" @back="openView('query')" @select-skill="selectSkill" @update:canary-percent="skillCanaryPercent=$event" @update:preview-subject="skillPreviewSubject=$event" @create="createSkillDraft" @evaluate="evaluateSkillDraft" @evaluate-query-quality="evaluateSkillQueryQuality" @canary="startSkillCanary" @publish="publishSkillVersion" @rollback="rollbackSkillVersion" @preview="previewSkillResolution" /></template>
