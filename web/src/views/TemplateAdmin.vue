<script setup lang="ts">
/**
 * 票种模板管理（管理端）：列表 / 详情 / 新版本 / 规则编辑 / 试跑。
 *
 * 后端在 S2-8 已完成；本页只做管理端 UI。规则随模板版本走：
 * `PUT /templates/{id}` 会生成 version+1 的新行并复制旧规则，所以本页的"保存"是"升版本"。
 */
import { ElMessage, type UploadFile } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'

import { ApiError } from '../api/http'
import {
  createTemplate,
  getTemplate,
  listTemplates,
  testTemplate,
  updateTemplate,
  type TemplateListParams,
} from '../api/templates'
import type {
  TemplateDetailData,
  TemplateOut,
  TemplateRuleInput,
  TemplateTestData,
} from '../types/template'
import { TICKET_TYPE_OPTIONS, VALIDATOR_OPTIONS } from '../types/template'
import { fieldLabel, formatDateTime, formatPercent } from '../utils/format'

interface RuleForm {
  field_name: string
  pattern: string
  validator: string
  error_msg: string
  priority: number
  active: boolean
}

const rows = ref<TemplateOut[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const activeOnly = ref(true)
const typeFilter = ref('')
const page = ref(1)
const pageSize = ref(20)

const drawerOpen = ref(false)
const detail = ref<TemplateDetailData | null>(null)
const detailLoading = ref(false)

const dialogOpen = ref(false)
const editingId = ref<number | null>(null)
const editingVersion = ref(1)
const saving = ref(false)
const form = reactive({
  type_code: '',
  type_name: '',
  keywordsText: '',
  specsText: '{}',
  active: false,
  rules: [] as RuleForm[],
})

const testLoading = ref(false)
const testResult = ref<TemplateTestData | null>(null)
const testError = ref('')

const detailSpecs = computed(() =>
  detail.value ? JSON.stringify(detail.value.template.field_specs_json, null, 2) : '',
)

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const params: TemplateListParams = {
      active: activeOnly.value,
      page: page.value,
      page_size: pageSize.value,
      sort_by: 'updated_at',
      order: 'desc',
    }
    if (typeFilter.value) params.type_code = typeFilter.value
    const data = await listTemplates(params)
    rows.value = data.items
    total.value = data.total
  } catch (cause) {
    rows.value = []
    total.value = 0
    error.value = cause instanceof ApiError ? cause.message : '加载失败，请重试'
  } finally {
    loading.value = false
  }
}

function changeFilter(): void {
  page.value = 1
  void load()
}

function changePage(next: number): void {
  page.value = next
  void load()
}

async function openDetail(row: { id: number }): Promise<void> {
  drawerOpen.value = true
  detailLoading.value = true
  testResult.value = null
  testError.value = ''
  try {
    detail.value = await getTemplate(row.id)
  } catch (cause) {
    drawerOpen.value = false
    ElMessage.error(cause instanceof ApiError ? cause.message : '模板详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

function openCreate(): void {
  editingId.value = null
  editingVersion.value = 1
  form.type_code = ''
  form.type_name = ''
  form.keywordsText = ''
  form.specsText = '{\n  "total_amount": {\n    "keywords": ["价税合计"],\n    "strategy": "total_zone"\n  }\n}'
  form.active = false
  form.rules = []
  dialogOpen.value = true
}

function openEdit(): void {
  if (!detail.value) return
  const template = detail.value.template
  editingId.value = template.id
  editingVersion.value = template.version
  form.type_code = template.type_code
  form.type_name = template.type_name
  form.keywordsText = (template.match_keywords ?? []).join(', ')
  form.specsText = JSON.stringify(template.field_specs_json, null, 2)
  form.active = template.active
  form.rules = detail.value.rules.map((rule) => ({
    field_name: rule.field_name,
    pattern: rule.pattern ?? '',
    validator: rule.validator ?? '',
    error_msg: rule.error_msg ?? '',
    priority: rule.priority,
    active: rule.active,
  }))
  dialogOpen.value = true
}

function addRule(): void {
  form.rules.push({
    field_name: '',
    pattern: '',
    validator: '',
    error_msg: '',
    priority: 100,
    active: true,
  })
}

function removeRule(index: number): void {
  form.rules.splice(index, 1)
}

function parseKeywords(): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const item of form.keywordsText.split(/[,，\n]/)) {
    const text = item.trim()
    if (text && !seen.has(text)) {
      seen.add(text)
      result.push(text)
    }
  }
  return result
}

function parseSpecs(): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(form.specsText) as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
    return parsed as Record<string, unknown>
  } catch {
    return null
  }
}

async function save(): Promise<void> {
  if (saving.value) return
  const keywords = parseKeywords()
  if (!form.type_code.trim()) {
    ElMessage.warning('请选择票种')
    return
  }
  if (!form.type_name.trim()) {
    ElMessage.warning('请输入模板名称')
    return
  }
  if (keywords.length < 1 || keywords.length > 10) {
    ElMessage.warning('匹配关键字必须 1–10 个')
    return
  }
  const specs = parseSpecs()
  if (!specs) {
    ElMessage.warning('字段规格必须是合法的 JSON 对象')
    return
  }
  const rules: TemplateRuleInput[] = form.rules
    .filter((rule) => rule.field_name.trim())
    .map((rule) => ({
      field_name: rule.field_name.trim(),
      pattern: rule.pattern.trim() || null,
      validator: rule.validator || null,
      error_msg: rule.error_msg.trim() || null,
      priority: Number(rule.priority) || 0,
      active: rule.active,
    }))

  const payload = {
    type_code: form.type_code,
    type_name: form.type_name.trim(),
    match_keywords: keywords,
    field_specs_json: specs,
    active: form.active,
    rules,
  }

  saving.value = true
  try {
    if (editingId.value !== null) {
      const updated = await updateTemplate(editingId.value, {
        ...payload,
        version: editingVersion.value,
      })
      ElMessage.success(`已生成 v${updated.version}`)
      dialogOpen.value = false
      await load()
      await openDetail(updated)
    } else {
      const created = await createTemplate(payload)
      ElMessage.success('模板已创建')
      dialogOpen.value = false
      await load()
      await openDetail(created)
    }
  } catch (cause) {
    ElMessage.error(cause instanceof ApiError ? cause.message : '保存失败，请重试')
  } finally {
    saving.value = false
  }
}

async function handleTestFile(uploadFile: UploadFile): Promise<void> {
  const raw = uploadFile.raw
  if (!raw || !detail.value) return
  testLoading.value = true
  testResult.value = null
  testError.value = ''
  try {
    testResult.value = await testTemplate(detail.value.template.id, raw as File)
  } catch (cause) {
    testError.value = cause instanceof ApiError ? cause.message : '试跑失败，请重试'
  } finally {
    testLoading.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <el-space direction="vertical" fill size="large" class="template-page">
    <div class="page-heading">
      <div>
        <h2>票种模板</h2>
        <p>维护票种关键字、字段规格与校验规则；保存会生成新版本，旧版本保留。</p>
      </div>
      <el-button type="primary" @click="openCreate">新建模板</el-button>
    </div>

    <el-card shadow="never">
      <div class="filters">
        <el-switch
          v-model="activeOnly"
          active-text="只看生效版本"
          @change="changeFilter"
        />
        <el-select
          v-model="typeFilter"
          placeholder="全部票种"
          clearable
          class="filter-type"
          @change="changeFilter"
        >
          <el-option
            v-for="item in TICKET_TYPE_OPTIONS"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-button @click="load">刷新</el-button>
        <span class="filter-note">共 {{ total }} 个版本</span>
      </div>

      <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" class="list-error">
        <el-button text type="primary" @click="load">重试</el-button>
      </el-alert>

      <el-table v-loading="loading" :data="rows" stripe row-key="id">
        <el-table-column label="模板" min-width="190">
          <template #default="{ row }">
            <div class="template-cell">
              <strong>{{ row.type_name }}</strong>
              <code>{{ row.type_code }}</code>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="匹配关键字" min-width="260">
          <template #default="{ row }">
            <el-tag
              v-for="keyword in row.match_keywords ?? []"
              :key="keyword"
              size="small"
              effect="plain"
              class="keyword-tag"
            >
              {{ keyword }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="版本" width="80">
          <template #default="{ row }">v{{ row.version }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.active ? 'success' : 'info'" effect="plain" size="small">
              {{ row.active ? '生效' : '历史' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" @click="openDetail(row)">查看</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="没有符合条件的模板版本" />
        </template>
      </el-table>

      <div class="pager">
        <el-pagination
          :current-page="page"
          :page-size="pageSize"
          :total="total"
          :page-count="pageCount"
          layout="total, prev, pager, next"
          @current-change="changePage"
        />
      </div>
    </el-card>

    <!-- 详情 / 规则 / 试跑 -->
    <el-drawer v-model="drawerOpen" size="760px" :title="detail?.template.type_name ?? '模板详情'">
      <div v-loading="detailLoading">
        <template v-if="detail">
          <div class="detail-head">
            <div class="detail-meta">
              <el-tag effect="plain">{{ detail.template.type_code }}</el-tag>
              <el-tag type="info" effect="plain">v{{ detail.template.version }}</el-tag>
              <el-tag :type="detail.template.active ? 'success' : 'info'" effect="plain">
                {{ detail.template.active ? '生效' : '历史版本' }}
              </el-tag>
            </div>
            <el-button type="primary" @click="openEdit">
              {{ detail.template.active ? '编辑并升版本' : '以此版本新建下一版' }}
            </el-button>
          </div>

          <el-tabs>
            <el-tab-pane label="字段规格">
              <pre class="json-view">{{ detailSpecs }}</pre>
            </el-tab-pane>

            <el-tab-pane :label="`校验规则（${detail.rules.length}）`">
              <el-table :data="detail.rules" size="small">
                <el-table-column prop="field_name" label="字段" width="120" />
                <el-table-column label="正则" min-width="180">
                  <template #default="{ row }">
                    <code class="pattern">{{ row.pattern ?? '—' }}</code>
                  </template>
                </el-table-column>
                <el-table-column prop="validator" label="校验器" width="110" />
                <el-table-column prop="priority" label="优先级" width="80" />
                <el-table-column label="启用" width="70">
                  <template #default="{ row }">{{ row.active ? '是' : '否' }}</template>
                </el-table-column>
                <template #empty>
                  <el-empty description="该版本没有单独规则" :image-size="60" />
                </template>
              </el-table>
            </el-tab-pane>

            <el-tab-pane label="试跑">
              <div class="test-panel">
                <el-upload
                  :auto-upload="false"
                  :show-file-list="false"
                  accept=".jpg,.jpeg,.png"
                  :disabled="!detail.template.active || testLoading"
                  :on-change="handleTestFile"
                >
                  <el-button :loading="testLoading" :disabled="!detail.template.active">
                    选择一张票据试跑
                  </el-button>
                </el-upload>
                <span v-if="!detail.template.active" class="test-hint">只有生效版本可以试跑</span>
                <span v-else class="test-hint">只支持 JPG/PNG，不落库、不写对象存储</span>
              </div>

              <el-alert
                v-if="testError"
                type="error"
                :closable="false"
                show-icon
                :title="testError"
                class="test-error"
              />

              <template v-if="testResult">
                <el-descriptions :column="3" border size="small" class="test-summary">
                  <el-descriptions-item label="命中关键字">
                    {{ testResult.matched_keywords.join('、') || '—' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="整体置信度">
                    {{ formatPercent(testResult.overall_confidence) }}
                  </el-descriptions-item>
                  <el-descriptions-item label="耗时">
                    {{ testResult.elapsed_ms }} ms
                  </el-descriptions-item>
                </el-descriptions>

                <el-table :data="testResult.fields" size="small">
                  <el-table-column label="字段" width="130">
                    <template #default="{ row }">{{ fieldLabel(row.field_name) }}</template>
                  </el-table-column>
                  <el-table-column prop="value" label="值" min-width="160" />
                  <el-table-column label="置信度" width="90">
                    <template #default="{ row }">{{ formatPercent(row.confidence) }}</template>
                  </el-table-column>
                  <el-table-column prop="source" label="来源" width="80" />
                  <template #empty>
                    <el-empty description="没有抽到字段" :image-size="60" />
                  </template>
                </el-table>
              </template>
            </el-tab-pane>
          </el-tabs>
        </template>
      </div>
    </el-drawer>

    <!-- 新建 / 升版本 -->
    <el-dialog
      v-model="dialogOpen"
      :title="editingId === null ? '新建模板' : `编辑模板（当前 v${editingVersion}，保存后生成 v${editingVersion + 1}）`"
      width="860px"
      destroy-on-close
    >
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item label="票种">
            <el-select v-model="form.type_code" placeholder="选择票种" :disabled="editingId !== null">
              <el-option
                v-for="item in TICKET_TYPE_OPTIONS"
                :key="item.value"
                :label="`${item.label}（${item.value}）`"
                :value="item.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="模板名称">
            <el-input v-model="form.type_name" maxlength="50" placeholder="例如：增值税电子普通发票" />
          </el-form-item>
          <el-form-item label="生效">
            <el-switch v-model="form.active" />
          </el-form-item>
        </div>

        <el-form-item label="匹配关键字（逗号分隔，1–10 个）">
          <el-input v-model="form.keywordsText" placeholder="增值税电子普通发票, 电子普通发票, 校验码" />
        </el-form-item>

        <el-form-item label="字段规格（JSON：字段 → 锚点/策略/正则）">
          <el-input v-model="form.specsText" type="textarea" :rows="10" class="json-input" />
        </el-form-item>

        <div class="rules-head">
          <span>校验规则</span>
          <el-button size="small" @click="addRule">加一条</el-button>
        </div>
        <el-table :data="form.rules" size="small" class="rules-table">
          <el-table-column label="字段" width="130">
            <template #default="{ row }">
              <el-input v-model="row.field_name" size="small" placeholder="total_amount" />
            </template>
          </el-table-column>
          <el-table-column label="正则" min-width="180">
            <template #default="{ row }">
              <el-input v-model="row.pattern" size="small" placeholder="可留空" />
            </template>
          </el-table-column>
          <el-table-column label="校验器" width="120">
            <template #default="{ row }">
              <el-select v-model="row.validator" size="small" clearable placeholder="无">
                <el-option
                  v-for="item in VALIDATOR_OPTIONS"
                  :key="item.value"
                  :label="item.label"
                  :value="item.value"
                />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="优先级" width="90">
            <template #default="{ row }">
              <el-input-number v-model="row.priority" size="small" :min="0" :max="999" />
            </template>
          </el-table-column>
          <el-table-column label="启用" width="60">
            <template #default="{ row }">
              <el-switch v-model="row.active" size="small" />
            </template>
          </el-table-column>
          <el-table-column label="" width="60">
            <template #default="{ $index }">
              <el-button text type="danger" size="small" @click="removeRule($index)">删</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-form>

      <template #footer>
        <el-button :disabled="saving" @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">
          {{ editingId === null ? '创建' : '保存并升版本' }}
        </el-button>
      </template>
    </el-dialog>
  </el-space>
</template>

<style scoped>
.template-page {
  width: 100%;
  max-width: none;
  margin: 0 auto;
}
.filters {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}
.filter-type {
  width: 240px;
}
.filter-note {
  margin-left: auto;
  font-size: 12px;
  color: var(--app-muted);
}
.template-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.template-cell code {
  font-size: 12px;
  color: var(--app-muted);
}
.keyword-tag {
  margin: 0 6px 4px 0;
}
.list-error {
  margin-bottom: 12px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.detail-meta {
  display: flex;
  gap: 8px;
}
.json-view {
  margin: 0;
  padding: 14px;
  max-height: 460px;
  overflow: auto;
  border-radius: 8px;
  background: #0f172a;
  color: #cbd5e1;
  font-size: 12px;
  line-height: 1.7;
}
.pattern {
  font-size: 12px;
  color: #475569;
  word-break: break-all;
}
.test-panel {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.test-hint {
  font-size: 12px;
  color: var(--app-muted);
}
.test-error {
  margin-bottom: 12px;
}
.test-summary {
  margin-bottom: 12px;
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 120px;
  gap: 12px;
}
.json-input :deep(textarea) {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
}
.rules-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 4px 0 8px;
  font-weight: 600;
}
.rules-table {
  margin-bottom: 8px;
}
</style>