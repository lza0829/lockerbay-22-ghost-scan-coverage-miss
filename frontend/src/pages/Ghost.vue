<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'

const data = ref(null)
const report = ref(null)
const loading = ref(false)
const coverage = ref(null)
const runs = ref([])
const reconcile = ref(null)

const KIND_LABEL = {
  cell_occupied_without_open_occupancy: '格口占用态 · 无 open 占用行',
  open_occupancy_but_cell_free: '有 open 占用 · 格口已 free',
}

async function load() {
  data.value = await api.ghost()
  runs.value = await api.ghostScanRuns()
  coverage.value = await api.ghostCoverage()
  reconcile.value = await api.ghostReconcile()
}
async function scan() {
  loading.value = true
  try {
    report.value = await api.ghostScan()
    await load()
  } finally {
    loading.value = false
  }
}
async function pick(runId) {
  coverage.value = await api.ghostCoverage(runId)
}
const verifyFailed = computed(() => (coverage.value?.verification?.checks || []).filter((c) => !c.ok))
onMounted(load)
</script>

<template>
  <div>
    <h1>幽灵占用核证</h1>
    <p class="sub">
      扫描只发现并整组入搁置（reason=ghost，保留两侧快照），不改格口 / 占用正式态；
      重复扫描按指纹幂等，不产生重复搁置。
    </p>
    <p>
      <button @click="scan" :disabled="loading">{{ loading ? '扫描中…' : '跑一次幽灵扫描' }}</button>
    </p>
    <pre v-if="report" class="panel">{{ report }}</pre>

    <div class="stats" v-if="data" style="margin-top:1rem">
      <div class="stat"><b>{{ data.counts.live }}</b><span>活态不一致对</span></div>
      <div class="stat"><b>{{ data.counts.total_ghost_holds }}</b><span>幽灵搁置累计</span></div>
      <div class="stat"><b>{{ data.counts.historical_only }}</b><span>仅历史搁置</span></div>
      <div class="stat" :class="{ 'stat-ghost': !coverage?.complete }">
        <b>{{ coverage?.complete ? '✓' : '✗' }}</b><span>本趟覆盖完整</span>
      </div>
    </div>

    <h2 class="gh-h2">覆盖成员证明（扫描标成功即同趟落库，可逐格复核）</h2>
    <div class="panel" style="overflow:auto">
      <div v-if="!coverage || !coverage.run_id" class="muted">尚无覆盖跑次，先跑一次幽灵扫描。</div>
      <template v-else>
        <div class="cov-head">
          <span>跑次 <code>{{ coverage.run_id }}</code></span>
          <span>已扫 <b>{{ coverage.scanned_n }}</b></span>
          <span>命中 <b>{{ coverage.hit_n }}</b></span>
          <span>held_n <b>{{ coverage.held_n }}</b></span>
          <span :class="coverage.complete ? 'ok' : 'bad'">
            {{ coverage.complete ? '覆盖完整' : '覆盖不完整' }}
          </span>
        </div>
        <div class="cov-cols">
          <div>
            <h3>已扫 cell_id（{{ coverage.scanned_cells.length }}）</h3>
            <div class="chips">
              <span
                v-for="c in coverage.scanned_cells"
                :key="c"
                class="chip"
                :class="{ hit: coverage.hit_cells.includes(c) }"
              >{{ c }}</span>
            </div>
          </div>
          <div>
            <h3>命中 cell_id（{{ coverage.hit_cells.length }}）</h3>
            <div class="chips">
              <span v-for="c in coverage.hit_cells" :key="c" class="chip hit">{{ c }}</span>
              <span v-if="!coverage.hit_cells.length" class="muted">（本趟无命中）</span>
            </div>
          </div>
        </div>
        <table style="margin-top:.75rem">
          <thead><tr><th>核证项</th><th>结论</th><th>细节</th></tr></thead>
          <tbody>
            <tr v-for="c in coverage.verification.checks" :key="c.check">
              <td>{{ c.check }}</td>
              <td :class="c.ok ? 'ok' : 'bad'">{{ c.ok ? '通过' : '不通过' }}</td>
              <td class="muted">{{ c.detail }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="verifyFailed.length" class="bad">
          覆盖核证不通过：{{ verifyFailed.map((c) => c.check).join('；') }}
        </p>
      </template>
    </div>

    <div class="panel" style="margin-top:.75rem" v-if="runs.length">
      <h3 style="margin:0 0 .5rem;font-size:.95rem">覆盖跑次</h3>
      <table>
        <thead><tr><th>跑次</th><th>时刻</th><th>已扫</th><th>命中</th><th>held_n</th><th>完整</th></tr></thead>
        <tbody>
          <tr
            v-for="r in runs"
            :key="r.run_id"
            :class="{ 'row-selected': r.run_id === coverage?.run_id }"
            style="cursor:pointer"
            @click="pick(r.run_id)"
          >
            <td><code>{{ r.run_id }}</code></td>
            <td>{{ r.scanned_at }}</td>
            <td>{{ r.scanned_n }}</td>
            <td>{{ r.hit_n }}</td>
            <td>{{ r.held_n }}</td>
            <td :class="r.complete ? 'ok' : 'bad'">{{ r.complete ? '是' : '否' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel" style="margin-top:.75rem" v-if="reconcile">
      <h3 style="margin:0 0 .5rem;font-size:.95rem">占用账对账（扫描范围 vs 活态幽灵）</h3>
      <p :class="reconcile.aligned ? 'ok' : 'bad'">
        {{ reconcile.aligned ? '扫描范围与占用账一致' : '扫描范围与占用账存在偏差' }}
        <span v-if="reconcile.reason" class="muted">· {{ reconcile.reason }}</span>
      </p>
      <div class="muted" v-if="reconcile.run_id">
        活态不一致 {{ reconcile.live_inconsistent }} · 已扫 {{ reconcile.scanned_n }} ·
        命中 {{ reconcile.hit_n }} · held_n {{ reconcile.held_n }}
        <div v-if="reconcile.live_not_scanned.length">活态落在扫描范围外：{{ reconcile.live_not_scanned }}</div>
        <div v-if="reconcile.hit_outside_scanned.length">命中落在已扫外：{{ reconcile.hit_outside_scanned }}</div>
        <div v-if="reconcile.fingerprint_mismatch.length">指纹漂移：{{ reconcile.fingerprint_mismatch }}</div>
      </div>
    </div>

    <h2 class="gh-h2">活态不一致对（扫描前即可见，搁置后仍可复核）</h2>
    <div class="panel" style="overflow:auto">
      <table>
        <thead>
          <tr><th>格口</th><th>漂移类型</th><th>格口侧快照</th><th>占用侧 open 行</th><th>搁置</th><th>指纹</th></tr>
        </thead>
        <tbody>
          <tr v-for="g in data.live_inconsistent" :key="g.fingerprint" class="row-ghost">
            <td><code>{{ g.cell_id }}</code></td>
            <td>{{ KIND_LABEL[g.kind] || g.kind }}</td>
            <td><code>{{ g.cell_snapshot }}</code></td>
            <td>
              <div v-if="g.open_occupancies.length === 0" class="muted">（无 open 占用行）</div>
              <div v-for="o in g.open_occupancies" :key="o.id">
                <code>{{ o.id }}</code> · {{ o.tracking_no }} · {{ o.status }}
              </div>
              <div class="muted" v-if="g.latest_occupancy">
                最近占用：<code>{{ g.latest_occupancy.id }}</code> · {{ g.latest_occupancy.status }}
              </div>
            </td>
            <td>
              <span v-if="g.quarantine" class="badge">已搁置 #{{ g.quarantine.id }}</span>
              <span v-else class="muted">待扫描</span>
            </td>
            <td><code class="fp">{{ g.fingerprint.slice(0, 10) }}…</code></td>
          </tr>
          <tr v-if="data.live_inconsistent.length === 0">
            <td colspan="6" class="muted">当前无活态不一致对。</td>
          </tr>
        </tbody>
      </table>
    </div>

    <h2 class="gh-h2">历史幽灵搁置（审计边，不删除）</h2>
    <div class="panel" style="overflow:auto">
      <table>
        <thead><tr><th>搁置#</th><th>格口</th><th>扫描</th><th>搁置时刻</th><th>搁置时快照</th></tr></thead>
        <tbody>
          <tr v-for="h in data.historical_holds" :key="h.quarantine_id">
            <td>#{{ h.quarantine_id }}</td>
            <td><code>{{ h.cell_id }}</code></td>
            <td><code>{{ h.scan_id }}</code></td>
            <td>{{ h.held_at }}</td>
            <td><code class="snap">{{ h.snapshot_at_hold }}</code></td>
          </tr>
          <tr v-if="data.historical_holds.length === 0">
            <td colspan="5" class="muted">无历史搁置。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.gh-h2 { font-size: 1rem; margin: 1.2rem 0 .5rem; font-weight: 600; }
.row-ghost td { background: rgba(196, 92, 106, .08); }
.muted { color: var(--muted); font-size: .82rem; }
.badge {
  display: inline-block; padding: .1rem .45rem; border-radius: 3px;
  background: rgba(196, 92, 106, .25); border: 1px solid rgba(196, 92, 106, .55);
  font-size: .78rem;
}
.fp, .snap { font-size: .76rem; word-break: break-all; }
.snap { display: inline-block; max-width: 420px; white-space: pre-wrap; }
.cov-head { display: flex; flex-wrap: wrap; gap: 1rem; align-items: center; font-size: .88rem; }
.cov-head b { font-family: var(--mono); color: var(--signal); }
.cov-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; margin-top: .75rem; }
.cov-cols h3 { font-size: .88rem; font-weight: 500; margin: 0 0 .4rem; color: var(--muted); }
.chips { display: flex; flex-wrap: wrap; gap: .3rem; }
.chip {
  font-family: var(--mono); font-size: .76rem; padding: .1rem .4rem; border-radius: 3px;
  border: 1px solid var(--line); background: var(--bg2); color: var(--muted);
}
.chip.hit { border-color: var(--over); color: var(--over); background: rgba(196, 92, 106, .12); }
.ok { color: var(--free); }
.bad { color: var(--over); }
.row-selected td { background: rgba(240, 196, 63, .08); }
</style>
