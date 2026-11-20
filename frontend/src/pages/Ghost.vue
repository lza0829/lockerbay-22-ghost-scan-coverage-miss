<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const data = ref(null)
const report = ref(null)
const loading = ref(false)

const KIND_LABEL = {
  cell_occupied_without_open_occupancy: '格口占用态 · 无 open 占用行',
  open_occupancy_but_cell_free: '有 open 占用 · 格口已 free',
}

async function load() { data.value = await api.ghost() }
async function scan() {
  loading.value = true
  try {
    report.value = await api.ghostScan()
    await load()
  } finally {
    loading.value = false
  }
}
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
</style>
