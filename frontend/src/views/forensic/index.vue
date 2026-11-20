<template>
  <section class="page" data-module="forensic">
    <header class="page-head">
      <div>
        <h2>取证封存管理</h2>
        <p class="page-desc">
          读取损益数据的分区范围、对账人确认与分区校验记录，从待核对流转到已封存或待作废；
          交付政策：有效分区补充件交付，签错隔离的分区不再随旧封存件发给新申请。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记取证封存</button>
        <button class="btn" type="button" @click="exportRows">导出取证封存清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>封存编号 / 快照编号</span>
        <input v-model="keyword" placeholder="按编号检索" />
      </label>
      <label class="filter-item">
        <span>状态</span>
        <select v-model="statusFilter">
          <option value="">全部</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td>{{ row['封存编号'] }}</td>
          <td>{{ row['快照编号'] }}</td>
          <td>第 {{ row['封存序号'] }} 次</td>
          <td>{{ row['损益期间'] }}</td>
          <td>{{ readyText(row) }}</td>
          <td>{{ deliverableText(row) }}</td>
          <td>{{ (row.receipts || []).length }} 份</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">明细</button>
            <button class="link" type="button" @click="runSimpleAction('确认封存', row)">确认封存</button>
            <button class="link" type="button" @click="runSimpleAction('恢复封存', row)">恢复封存</button>
            <button class="link" type="button" @click="runSimpleAction('申请作废', row)">申请作废</button>
            <button class="link" type="button" @click="runSimpleAction('确认作废', row)">确认作废</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无取证封存数据，可先登记取证封存</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条取证封存记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
    </footer>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记取证封存</h3>
            <button class="btn ghost" type="button" @click="showCreateModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCreate">
            <label class="modal-field">
              <span>快照编号</span>
              <input v-model="createFields['快照编号']" placeholder="如 SNAP-0003" required />
            </label>
            <label class="modal-field">
              <span>损益期间</span>
              <input v-model="createFields['损益期间']" placeholder="如 2026-09" required />
            </label>
            <label class="modal-field">
              <span>数据集名称（留空则按快照编号带出）</span>
              <input v-model="createFields['数据集名称']" placeholder="损益数据集名称" />
            </label>
            <label class="modal-field">
              <span>分区明细（每行一条：分区号,分区范围,行数,校验哈希）</span>
              <textarea
                v-model="createPartitionsText"
                rows="4"
                placeholder="P1,2026-09-01~2026-09-10,1250,a1b2c3-p1"
                required
              ></textarea>
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showCreateModal = false">取消</button>
              <button class="btn primary" type="submit">提交登记</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="detail" class="modal-overlay" @click.self="closeDetail">
        <div class="modal-card wide">
          <header class="modal-head">
            <h3>{{ detail['封存编号'] }} · {{ detail.status }}（{{ detail['交付政策'] }}）</h3>
            <button class="btn ghost" type="button" @click="closeDetail">&times;</button>
          </header>
          <div class="modal-body">
            <section class="detail-block">
              <h4>交付清单</h4>
              <p v-if="manifest" class="manifest-line">
                当前 {{ manifest['交付类型'] }}<template v-if="manifest['可交付']">，可交付 {{ manifest['可交付证据'].length }} 份证据</template><template v-else>，暂不可交付</template>；
                <span v-if="manifest['隔离证据'].length">隔离 {{ manifest['隔离证据'].length }} 份（{{ manifest['隔离证据'].map((e: any) => e['证据编号']).join('、') }}）；</span>
                <span v-if="manifest['待确认分区'].length">待确认分区：{{ manifest['待确认分区'].join('、') }}；</span>
                清单更新于 {{ manifest['交付清单更新时间'] || '—' }}
              </p>
            </section>

            <section class="detail-block">
              <h4>分区证据</h4>
              <table class="data-table">
                <thead>
                  <tr>
                    <th>分区号</th><th>分区范围</th><th>校验记录</th><th>对账确认</th>
                    <th>证据编号</th><th>证据状态</th><th>分区动作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="part in detail.partitions" :key="part['分区号']">
                    <td>{{ part['分区号'] }}</td>
                    <td>
                      {{ part['分区范围'] }}
                      <div v-for="(c, i) in part['更正记录']" :key="i" class="muted-line">
                        更正：{{ c['原签署范围'] }} → {{ c['正确范围'] }}（{{ c['说明'] }}）
                      </div>
                    </td>
                    <td>{{ part['校验记录']['行数'] }} 行 / {{ part['校验记录']['校验哈希'] }}</td>
                    <td>
                      <template v-if="part['确认']">
                        {{ part['确认']['对账人'] }} · 第{{ part['确认']['确认轮次'] }}轮<br />
                        <span class="muted-line">{{ part['确认']['签署范围'] }} · {{ part['确认']['确认时间'] }}</span>
                      </template>
                      <span v-else class="muted-line">未确认</span>
                    </td>
                    <td>{{ part['证据编号'] }}</td>
                    <td>{{ part['证据状态'] }}</td>
                    <td class="part-actions">
                      <template v-if="part['证据状态'] === '待确认'">
                        <input v-model="partForms[part['分区号']]['对账人']" placeholder="对账人" />
                        <input v-model="partForms[part['分区号']]['签署范围']" placeholder="签署范围" />
                        <button class="link" type="button" @click="runPartAction('确认分区', part)">确认分区</button>
                      </template>
                      <template v-if="part['证据状态'] === '有效' && (detail.status === '已封存' || detail.status === '待作废')">
                        <input v-model="partForms[part['分区号']]['正确范围']" placeholder="正确范围" />
                        <input v-model="partForms[part['分区号']]['说明']" placeholder="说明（签错范围）" />
                        <button class="link" type="button" @click="runPartAction('登记更正', part)">登记更正</button>
                      </template>
                      <template v-if="part['证据状态'] === '签错隔离'">
                        <input v-model="partForms[part['分区号']]['对账人']" placeholder="对账人" />
                        <input v-model="partForms[part['分区号']]['签署范围']" placeholder="签署范围（=更正后范围）" />
                        <button class="link" type="button" @click="runPartAction('重签分区', part)">重签分区</button>
                      </template>
                    </td>
                  </tr>
                </tbody>
              </table>
            </section>

            <section class="detail-block">
              <h4>交付领取</h4>
              <div class="deliver-bar">
                <input v-model="receiver" placeholder="领取人" />
                <button class="btn" type="button" @click="deliver">交付领取</button>
                <span class="muted-line">待核对不可交付；待作废按政策交付有效分区补充件</span>
              </div>
              <table class="data-table">
                <thead>
                  <tr><th>领取编号</th><th>领取人</th><th>交付类型</th><th>领取时间</th><th>证据份数</th><th>核对</th></tr>
                </thead>
                <tbody>
                  <tr v-for="rc in detail.receipts" :key="rc['领取编号']">
                    <td>{{ rc['领取编号'] }}</td>
                    <td>{{ rc['领取人'] }}</td>
                    <td>{{ rc['交付类型'] }}</td>
                    <td>{{ rc['领取时间'] }}</td>
                    <td>{{ rc['证据清单'].length }} 份</td>
                    <td class="row-actions">
                      <button class="link" type="button" @click="compareReceipt(rc)">对照新申请</button>
                      <button class="link" type="button" @click="verifyReceipt(rc)">核对领取时证据</button>
                    </td>
                  </tr>
                  <tr v-if="!detail.receipts.length">
                    <td colspan="6" class="empty-state">暂无领取记录</td>
                  </tr>
                </tbody>
              </table>
              <div v-if="compareResult" class="result-box">
                <h5>对照结果：{{ compareResult['领取编号'] }}（{{ compareResult['领取人'] }} · {{ compareResult['交付类型'] }}）</h5>
                <p>{{ compareResult['结论'] }}</p>
                <p class="muted-line">新申请可取：{{ compareResult['新申请可取'].map((e: any) => `${e['分区号']}/${e['证据编号']}`).join('、') || '无' }}</p>
                <p class="muted-line">新申请不可取：{{ compareResult['新申请不可取'].map((e: any) => `${e['分区号']}/${e['证据编号']}（${e['当前证据状态']}）`).join('、') || '无' }}</p>
              </div>
              <div v-if="verifyResult" class="result-box">
                <h5>领取时证据核对：{{ verifyResult['领取编号'] }} — {{ verifyResult['结论'] }}</h5>
                <p v-for="line in verifyResult['明细']" :key="line['证据编号']" class="muted-line">
                  {{ line['分区号'] }} / {{ line['证据编号'] }}：当前{{ line['当前证据状态'] }}，哈希{{ line['哈希一致'] ? '一致' : '不一致' }}
                </p>
              </div>
            </section>

            <section class="detail-block">
              <h4>流转记录</h4>
              <ul class="trail-list">
                <li v-for="(event, i) in detail['流转记录']" :key="i">
                  <span class="muted-line">{{ event['时间'] }}</span> · <strong>{{ event['动作'] }}</strong> — {{ event['说明'] }}
                </li>
              </ul>
            </section>
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/forensic'
const columns = ['封存编号', '快照编号', '封存序号', '损益期间', '证据齐备', '可交付', '领取份数', '状态']
const statuses = ['待核对', '已封存', '待作废', '已作废']
const stats = [
  { label: '待核对', value: 0 },
  { label: '已封存', value: 0 },
  { label: '待作废', value: 0 },
  { label: '已作废', value: 0 },
]

const rows = ref<Row[]>([])
const total = ref(0)
const keyword = ref('')
const statusFilter = ref('')
const errorMessage = ref('')
const noticeMessage = ref('')
const showCreateModal = ref(false)
const createFields = ref<Record<string, string>>({})
const createPartitionsText = ref('')

const detail = ref<Row | null>(null)
const manifest = ref<Row | null>(null)
const partForms = ref<Record<string, Record<string, string>>>({})
const receiver = ref('')
const compareResult = ref<Row | null>(null)
const verifyResult = ref<Row | null>(null)

function readyText(row: Row) {
  const parts = row.partitions || []
  const ready = parts.filter((p: Row) => p['证据状态'] === '有效').length
  return `${ready}/${parts.length}`
}

function deliverableText(row: Row) {
  const valid = (row.partitions || []).filter((p: Row) => p['证据状态'] === '有效' && p['确认']).length
  if (row.status === '已封存') return `整件 ${valid} 份`
  if (row.status === '待作废') return valid ? `补充件 ${valid} 份` : '不可交付'
  return '不可交付'
}

function updateStats() {
  stats[0].value = rows.value.filter((r) => r.status === '待核对').length
  stats[1].value = rows.value.filter((r) => r.status === '已封存').length
  stats[2].value = rows.value.filter((r) => r.status === '待作废').length
  stats[3].value = rows.value.filter((r) => r.status === '已作废').length
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = ''
  createFields.value = {}
  createPartitionsText.value = ''
  showCreateModal.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  const partitions = createPartitionsText.value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [partNo, range, count, hash] = line.split(',').map((cell) => cell.trim())
      return { 分区号: partNo, 分区范围: range, 行数: Number(count) || 0, 校验哈希: hash }
    })
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createFields.value, partitions } }),
    })
    const data = await response.json()
    if (!data.ok) throw new Error(data.message || '登记失败')
    showCreateModal.value = false
    noticeMessage.value = data.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  }
}

async function runSimpleAction(action: string, row: Row) {
  await postAction(row.id, { action })
}

async function runPartAction(action: string, part: Row) {
  if (!detail.value) return
  const form = partForms.value[part['分区号']] || {}
  await postAction(detail.value.id, { action, 分区号: part['分区号'], ...form })
}

async function deliver() {
  if (!detail.value) return
  await postAction(detail.value.id, { action: '交付领取', 领取人: receiver.value })
  receiver.value = ''
}

async function postAction(id: number, values: Record<string, unknown>) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const data = await response.json()
    if (!data.ok) throw new Error(data.message || '动作未生效')
    noticeMessage.value = data.message
    await reload()
    if (detail.value) await openDetail({ id: detail.value.id })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '取证封存操作失败'
  }
}

async function openDetail(row: Row) {
  compareResult.value = null
  verifyResult.value = null
  const [entryResp, manifestResp] = await Promise.all([
    request(`${ENDPOINT}/${row.id}`),
    request(`${ENDPOINT}/${row.id}/manifest`),
  ])
  detail.value = await entryResp.json()
  manifest.value = await manifestResp.json()
  const forms: Record<string, Record<string, string>> = {}
  for (const part of detail.value?.partitions || []) {
    forms[part['分区号']] = {
      对账人: '',
      签署范围: part['分区范围'],
      正确范围: '',
      说明: '确认人签错范围',
    }
  }
  partForms.value = forms
}

function closeDetail() {
  detail.value = null
  manifest.value = null
  compareResult.value = null
  verifyResult.value = null
}

async function compareReceipt(rc: Row) {
  if (!detail.value) return
  verifyResult.value = null
  const response = await request(`${ENDPOINT}/${detail.value.id}/receipts/${rc['领取编号']}/compare`)
  compareResult.value = await response.json()
}

async function verifyReceipt(rc: Row) {
  if (!detail.value) return
  compareResult.value = null
  const response = await request(`${ENDPOINT}/${detail.value.id}/receipts/${rc['领取编号']}/verify`)
  verifyResult.value = await response.json()
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error('取证封存列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '取证封存列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.filter-item select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; }
.notice-text { color: #067647; }
.modal-card.wide { width: 960px; max-width: 95vw; }
.detail-block { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
.detail-block h4 { margin: 0 0 8px; font-size: 14px; }
.detail-block h5 { margin: 0 0 6px; font-size: 13px; }
.muted-line { color: var(--muted); font-size: 12px; margin: 2px 0; }
.manifest-line { margin: 0; font-size: 13px; }
.part-actions { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.part-actions input { width: 150px; padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 12px; }
.deliver-bar { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.deliver-bar input { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; }
.result-box { border: 1px dashed var(--border); border-radius: 8px; padding: 8px 10px; margin-top: 8px; background: #fcfcfd; }
.trail-list { margin: 0; padding-left: 18px; font-size: 13px; }
textarea { padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; font-family: inherit; }
</style>
