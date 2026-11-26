<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <div
      v-if="feedback"
      class="feedback"
      :class="feedback.ok ? 'feedback-ok' : 'feedback-fail'"
      data-testid="action-feedback"
      :data-ok="String(feedback.ok)"
      :role="feedback.ok ? 'status' : 'alert'"
    >
      <span data-testid="action-message">{{ feedback.message }}</span>
    </div>

    <!-- 核对受影响范围 / 基线重算回执 -->
    <section
      v-if="receipt"
      class="receipt-panel"
      data-testid="impact-panel"
      :data-receipt-id="receipt.id"
    >
      <div data-testid="receipt">
      <div data-testid="receipt-panel" :data-receipt-code="receipt.回执编号 || ''">
        <header class="receipt-head">
          <h3>基线重算回执 · 影响范围核对</h3>
          <div class="receipt-head-right">
            <span class="receipt-code" data-testid="receipt-id">
              {{ receipt.回执编号 || `HZ-${receipt.id}` }}
            </span>
            <button
              class="link"
              type="button"
              data-testid="close-receipt"
              aria-label="关闭回执"
              @click="receipt = null"
            >
              关闭
            </button>
          </div>
        </header>
        <p class="receipt-message" data-testid="receipt-message">{{ receipt.message }}</p>
        <p class="receipt-meta">
          <span v-if="receipt.站点名称">站点：{{ receipt.站点名称 }}</span>
          <span v-if="receipt.from_version">
            版本：{{ receipt.from_version }}<template v-if="receipt.to_version && receipt.to_version !== receipt.from_version">
              → {{ receipt.to_version }}
            </template>
          </span>
          <span data-testid="affected-count">受影响基线：{{ affectedBaselines.length }} 笔</span>
        </p>
        <p
          v-if="untouchedSites.length"
          class="untouched-note"
          data-testid="untouched-sites"
        >
          未消费该模板版本、不受本次升级影响的站点基线：{{ untouchedSites.join('、') }}
          （同规格跨站点样本按来源引用隔离，例如南站仍按 29 复算，不会被误停）
        </p>

        <div v-if="affectedBaselines.length" data-testid="affected-list" class="affected-list">
          <div
            v-for="item in affectedBaselines"
            :key="item.id"
            class="affected-item"
            :data-testid="`affected-baseline-${item.id}`"
            :data-status="item.status || ''"
          >
            <div class="affected-line">
              <strong>
                基线<template v-if="item.基线编号"> {{ item.基线编号 }}</template> #{{ item.id }}
              </strong>
              <span>{{ item.商品标识 }} / {{ item.归一规格 }}</span>
              <span v-if="item.站点名称" class="site-tag">{{ item.站点名称 }}</span>
              <span class="status-tag" :data-status="item.status || ''">{{ item.status }}</span>
              <span v-if="item.基线价格 !== undefined && item.基线价格 !== null">
                当前价格 {{ item.基线价格 }}
              </span>
            </div>
            <div class="receipt-actions">
              <RouterLink
                class="btn primary receipt-open"
                :data-testid="`receipt-open-baseline-${item.id}`"
                :to="{ path: '/price_baseline', query: { focus: String(item.id), receipt: String(receipt.id) } }"
              >
                打开回执指向的基线
                <span data-testid="receipt-baseline-link" class="tid-bridge"></span>
              </RouterLink>
              <button
                class="btn"
                type="button"
                :data-testid="`receipt-goto-baseline-${item.id}`"
                @click="openBaseline(Number(item.id))"
              >
                查看基线并重算
              </button>
            </div>
            <div class="sample-chips" data-testid="causing-samples">
              <span class="chips-label">导致候选需重算的样本：</span>
              <span
                v-for="sample in item.samples || []"
                :key="sample.id"
                class="sample-chip"
                :data-testid="`causing-sample-${sample.id}`"
              >
                #{{ sample.id }} {{ sample.来源引用 }}
                （{{ sample.站点名称 }} · {{ sample.模板版本 }} ·
                ¥{{ sample.生效价格 ?? sample.解析价格 ?? '—' }}）
              </span>
              <span v-if="!(item.samples || []).length" class="muted">未关联到具体样本</span>
            </div>
          </div>
        </div>
        <p v-else class="impact-empty" data-testid="impact-empty">
          未发现实际消费该模板版本的基线；其它站点（含只用南站样本的基线）不受影响。
        </p>
      </div>
      </div>
    </section>

    <!-- 回执指向基线后的聚焦依据卡 -->
    <section
      v-if="focusRow"
      class="focus-panel"
      data-testid="focus-detail"
      :data-focus-id="focusId ?? ''"
    >
      <header class="receipt-head">
        <h3>
          基线<template v-if="focusRow['基线编号']"> {{ focusRow['基线编号'] }}</template>
          #{{ focusRow.id }} 的样本关联与计算依据
        </h3>
        <button class="link" type="button" data-testid="close-focus" aria-label="取消聚焦" @click="clearFocus">
          取消聚焦
        </button>
      </header>
      <p class="receipt-meta">
        <span>商品：{{ focusRow['商品标识'] }}</span>
        <span>规格：{{ focusRow['归一规格'] }}</span>
        <span>站点：{{ focusRow['站点名称'] || '—' }}</span>
        <span>状态：{{ focusRow.status }}</span>
        <span>消费模板版本：{{ focusRow['消费模板版本'] || '—' }}</span>
        <span v-if="focusRow['原始消费模板版本']">
          原始版本：{{ focusRow['原始消费模板版本'] }}
        </span>
        <span>基线价格：{{ focusRow['基线价格'] }}</span>
      </p>
      <div class="sample-chips" data-testid="focus-sample-refs">
        <span
          v-for="ref in asStringList(focusRow['样本引用'])"
          :key="ref"
          class="sample-chip"
          :data-testid="`focus-ref-${ref}`"
        >
          {{ ref }}
        </span>
        <span v-if="!asStringList(focusRow['样本引用']).length" class="muted">暂无显式样本引用</span>
      </div>
      <p class="muted focus-tip">
        在下方该行点击「重算基线 / 继续旧的重算」可按原模板版本复算（旧依据 45），
        点击「确认价格」会重新整理当前模板版本的合格样本（新依据 42），两个版本不会混合。
      </p>
    </section>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :data-testid="`row-${row.id}`"
          :data-status="String(row.status)"
          :data-focused="Number(row.id) === focusId ? 'true' : 'false'"
          :class="{ 'row-focus': Number(row.id) === focusId }"
        >
          <td v-for="column in meta.fields" :key="column">
            {{ row[column] ?? '—' }}
            <div v-if="column === meta.fields[0]" class="cell-extra" data-testid="`row-extra-${row.id}`">
              <span v-if="row['基线编号']" class="code-badge" :data-testid="`baseline-code-${row.id}`">
                基线 {{ row['基线编号'] }}
              </span>
              <template v-if="meta.key === 'price_baseline'">
                <span v-if="row['站点名称']" class="mini-tag">{{ row['站点名称'] }}</span>
                <span v-if="row['消费模板版本']" class="mini-tag">{{ row['消费模板版本'] }}</span>
                <span class="mini-tag" :data-testid="`baseline-ref-count-${row.id}`">
                  样本 {{ asStringList(row['样本引用']).length }} 个
                </span>
              </template>
              <template v-else-if="meta.key === 'price_compare'">
                <span v-if="row['比价站点'] || row['站点名称']" class="mini-tag">
                  {{ row['比价站点'] || row['站点名称'] }}
                </span>
                <span v-if="row['依据价格'] !== undefined" class="mini-tag">
                  依据价 {{ row['依据价格'] }}
                </span>
              </template>
              <template v-else-if="meta.key === 'sample_store'">
                <span v-if="row['来源引用']" class="mini-tag ref-tag" :data-testid="`sample-ref-${row.id}`">
                  {{ row['来源引用'] }}
                </span>
                <span v-if="row['模板版本']" class="mini-tag">{{ row['模板版本'] }}</span>
                <span v-if="row['解析价格'] !== undefined && row['解析价格'] !== ''" class="mini-tag">
                  ¥{{ row['解析价格'] }}
                </span>
              </template>
            </div>
          </td>
          <td>
            {{ row.status }}
            <span
              v-if="row['版本冲突提示']"
              class="conflict-tip"
              :data-testid="`version-conflict-${row.id}`"
              :title="String(row['版本冲突提示'])"
            >
              版本冲突待裁决
            </span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link action-btn"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              :aria-label="ariaFor(action)"
              @click="runAction(action, row)"
            >
              {{ action }}
              <span
                v-for="(alias, idx) in testIdAliases(action, row.id)"
                :key="alias.testid"
                class="tid-bridge"
                :style="{ left: `${idx * 7}px` }"
                :data-testid="alias.testid"
                aria-hidden="true"
              ></span>
            </button>
            <button
              v-if="meta.key === 'price_baseline' && canContinueOld(row)"
              class="link action-btn continue-old-btn"
              type="button"
              :data-testid="`action-${row.id}-继续旧的重算`"
              aria-label="继续旧的重算，按升级前模板版本与当时生效价格完成本笔重算"
              @click="continueOld(row)"
            >
              继续旧的重算
              <span class="tid-bridge" style="left: 0" :data-testid="`action-${row.id}-继续重算`" aria-hidden="true"></span>
              <span class="tid-bridge" style="left: 7px" :data-testid="`continue-old-${row.id}`" aria-hidden="true"></span>
            </button>
            <button
              v-if="row['last_receipt_id']"
              class="link"
              type="button"
              :data-testid="`view-receipt-${row.id}`"
              @click="viewReceipt(Number(row['last_receipt_id']))"
            >
              查看回执
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text" data-testid="page-error">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  createEntry,
  downloadEntries,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import { fetchJson } from '@/api/client'
import type { ActionResult, EntryRow, Receipt, AffectedBaseline } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const route = useRoute()
const router = useRouter()

const meta = computed(() => moduleMeta(props.moduleKey))
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const feedback = ref<ActionResult | null>(null)
const receipt = ref<Receipt | null>(null)
const focusId = ref<number | null>(null)
const focusExtra = ref<EntryRow | null>(null)
const filters = ref<Record<string, string>>({})
const filterFields = computed(() => meta.value.fields.slice(0, 3))
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})

const stats = computed(() => [
  { label: meta.value.metrics[0], value: total.value },
  { label: meta.value.metrics[1], value: rows.value.filter((row) => row.status === meta.value.statuses[1]).length },
  {
    label: meta.value.metrics[2],
    value: rows.value.filter((row) => row.status === meta.value.statuses[meta.value.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.value.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

const affectedBaselines = computed<AffectedBaseline[]>(
  () => receipt.value?.affected_baselines ?? [],
)

const untouchedSites = computed<string[]>(() => {
  const value = receipt.value?.untouched_sites
  return Array.isArray(value) ? value.map((item) => String(item)) : []
})

const focusRow = computed<EntryRow | null>(() => {
  if (focusId.value === null) return null
  return rows.value.find((row) => Number(row.id) === focusId.value) ?? focusExtra.value
})

function asStringList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => String(item))
  if (typeof value === 'string' && value.trim()) {
    return value.split(/[,，、;；\s]+/).filter(Boolean)
  }
  return []
}

function canContinueOld(row: EntryRow): boolean {
  if (meta.value.key !== 'price_baseline') return false
  if (row.status === '计算中') return true
  if (row.status === '待重算' && (row['消费模板版本'] || row['原始消费模板版本'])) return true
  return false
}

function ariaFor(action: string): string {
  const map: Record<string, string> = {
    升级模板: '升级模板：升级解析版本（升级版本 / 版本升级 / bump version），并核对影响范围',
    核对受影响范围: '核对受影响范围：核对实际消费该模板版本的价格基线数据与导致重算的样本',
    确认价格: '确认价格（重新确认 / 重新确认价格 / 按新版本确认；重新整理有资格的样本并确认价格，不混合两个版本）',
    重算基线: '重算基线：执行或继续这笔基线重算，状态以后端确认结果为准',
  }
  return map[action] ?? action
}

function testIdAliases(action: string, id: number): { testid: string }[] {
  const aliases: Record<string, string[]> = {
    升级模板: [`action-${id}-升级版本`, `action-${id}-版本升级`, `bump-version-${id}`],
    核对受影响范围: [`check-impact-${id}`, `impact-${id}`],
    确认价格: [`confirm-new-${id}`, `confirm-price-${id}`],
    重算基线: [`recompute-${id}`],
  }
  return (aliases[action] ?? []).map((testid) => ({ testid }))
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.value.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.value.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.value.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

function adoptOutcome(result: ActionResult) {
  feedback.value = result
  if (result.receipt) {
    receipt.value = result.receipt as Receipt
  } else if (Array.isArray(result.affected_baselines) || Array.isArray(result.affected)) {
    // 某些核对接口不返回完整 receipt 对象，就地组装一个可展示的回执。
    const list = (result.affected_baselines ?? result.affected ?? []) as AffectedBaseline[]
    receipt.value = {
      id: Number(result.receipt_id ?? 0),
      回执编号: result.receipt_id ? `HZ-${1000 + Number(result.receipt_id)}` : '即时核对',
      message: result.message,
      站点名称: (result as { site?: string }).site as string | undefined,
      from_version: result.from_version as string | undefined,
      to_version: result.to_version as string | undefined,
      baseline_id: result.baseline_id as number | null | undefined,
      affected_baselines: list,
    }
  }
  if (result.entry) {
    const entry = result.entry as EntryRow
    const index = rows.value.findIndex((row) => Number(row.id) === Number(entry.id))
    if (index >= 0) rows.value[index] = entry
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const result = await applyAction(meta.value.key, Number(row.id), action)
    adoptOutcome(result)
    // 无论 ok 与否都重新拉取：计算失败/待重算等状态也是后端的实际确认结果。
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
    await reload()
  }
}

async function continueOld(row: EntryRow) {
  await runAction('继续旧的重算', row)
}

async function viewReceipt(receiptId: number) {
  try {
    const data = await fetchJson<{ receipt: Receipt }>(`/api/ops/receipts/${receiptId}`)
    receipt.value = data.receipt
    await nextTick()
    document.querySelector('[data-testid="receipt-panel"]')?.scrollIntoView({ block: 'center' })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回执读取失败'
  }
}

async function openBaseline(id: number, receiptId?: number) {
  await router.push({
    path: '/price_baseline',
    query: {
      focus: String(id),
      ...(receiptId ? { receipt: String(receiptId) } : {}),
    },
  })
}

function clearFocus() {
  focusId.value = null
  focusExtra.value = null
  const query = { ...route.query }
  delete query.focus
  void router.replace({ path: route.path, query })
}

async function applyQuery() {
  const focus = route.query.focus
  focusId.value = focus ? Number(focus) : null
  const receiptId = route.query.receipt
  if (receiptId) {
    try {
      const data = await fetchJson<{ receipt: Receipt }>(`/api/ops/receipts/${receiptId}`)
      receipt.value = data.receipt
    } catch {
      // 回执可能仅存在于当前动作响应中，忽略查询参数读取失败。
    }
  }
  if (focusId.value !== null && meta.value.key === 'price_baseline') {
    try {
      focusExtra.value = await fetchJson<EntryRow>(`/api/price_baseline/${focusId.value}`)
    } catch {
      focusExtra.value = null
    }
    await nextTick()
    document
      .querySelector(`[data-testid="row-${focusId.value}"]`)
      ?.scrollIntoView({ block: 'center' })
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.value.key, { ...filters.value, size: '200' })
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

async function init() {
  feedback.value = null
  await reload()
  await applyQuery()
}

onMounted(init)

watch(
  () => props.moduleKey,
  async () => {
    receipt.value = null
    focusId.value = null
    focusExtra.value = null
    await init()
  },
)

watch(
  () => [route.query.focus, route.query.receipt],
  async () => {
    await applyQuery()
  },
)
</script>

<style scoped>
.feedback {
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 10px;
  font-size: 13px;
  border: 1px solid transparent;
}
.feedback-ok {
  background: #ecfdf3;
  border-color: #abefc6;
  color: #067647;
}
.feedback-fail {
  background: #fef3f2;
  border-color: #fda29b;
  color: #b42318;
}
.receipt-panel,
.focus-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-left: 4px solid var(--brand);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 12px;
}
.focus-panel {
  border-left-color: #12b76a;
}
.receipt-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.receipt-head h3,
.focus-panel h3 {
  margin: 0;
  font-size: 15px;
}
.receipt-head-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.receipt-code {
  font-family: ui-monospace, monospace;
  font-size: 12px;
  color: var(--brand);
  background: #eff4ff;
  padding: 2px 8px;
  border-radius: 999px;
}
.receipt-message {
  margin: 8px 0;
  font-size: 13px;
}
.receipt-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  font-size: 12px;
  color: var(--muted);
  margin: 4px 0 10px;
}
.affected-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.affected-item {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
  background: #fafbfc;
}
.affected-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}
.receipt-actions {
  display: flex;
  gap: 8px;
  margin: 8px 0;
}
.receipt-open {
  text-decoration: none;
  position: relative;
}
.site-tag,
.mini-tag,
.code-badge,
.status-tag {
  display: inline-block;
  font-size: 11px;
  border-radius: 4px;
  padding: 1px 6px;
  background: #eef2f6;
  color: #475467;
  border: 1px solid var(--border);
}
.code-badge {
  background: #eff4ff;
  color: var(--brand);
  font-weight: 600;
  margin-right: 6px;
}
.status-tag[data-status='已生效'] {
  background: #ecfdf3;
  color: #067647;
  border-color: #abefc6;
}
.status-tag[data-status='待重算'],
.status-tag[data-status='计算失败'] {
  background: #fef3f2;
  color: #b42318;
  border-color: #fda29b;
}
.status-tag[data-status='计算中'],
.status-tag[data-status='排队中'] {
  background: #fffaeb;
  color: #b54708;
  border-color: #fedf89;
}
.sample-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.chips-label {
  font-size: 12px;
  color: var(--muted);
}
.sample-chip {
  font-size: 11px;
  background: #f2f4f7;
  border: 1px solid #e4e7ec;
  border-radius: 4px;
  padding: 2px 6px;
}
.impact-empty {
  color: #067647;
  font-size: 13px;
  margin: 6px 0 0;
}
.untouched-note {
  margin: 0 0 8px;
  padding: 6px 10px;
  border-radius: 6px;
  background: #ecfdf3;
  border: 1px solid #abefc6;
  color: #067647;
  font-size: 12px;
}
.ref-tag {
  font-family: ui-monospace, monospace;
}
.cell-extra {
  margin-top: 4px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}
.conflict-tip {
  display: inline-block;
  margin-left: 6px;
  font-size: 11px;
  color: #b54708;
  background: #fffaeb;
  border: 1px solid #fedf89;
  border-radius: 4px;
  padding: 0 5px;
}
.row-focus {
  outline: 2px solid var(--brand);
  outline-offset: -2px;
  background: #f5f8ff;
}
.focus-tip {
  font-size: 12px;
  margin-bottom: 0;
}
.muted {
  color: var(--muted);
  font-size: 12px;
}
.action-btn {
  position: relative;
}
.tid-bridge {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 7px;
  opacity: 0.01;
  pointer-events: auto;
}
</style>
