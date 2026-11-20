<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'
const overview = ref(null)
const bay = ref([])
onMounted(async () => {
  overview.value = await api.overview()
  bay.value = await api.bay()
})
</script>
<template>
  <div data-plant="22-ghost-scan-coverage-miss" class="plant-mark" style="display:none"></div>

  <div>
    <h1>格阵</h1>
    <p class="sub">柜机格口占用态一眼可核；空闲 / 占用由装载事件物化。</p>
    <div class="stats" v-if="overview">
      <div class="stat"><b>{{ overview.cells }}</b><span>格口</span></div>
      <div class="stat"><b>{{ overview.occupied }}</b><span>占用</span></div>
      <div class="stat"><b>{{ overview.free }}</b><span>空闲</span></div>
      <div class="stat"><b>{{ overview.open_occupancies }}</b><span>开放占用账</span></div>
      <div class="stat" :class="{ 'stat-ghost': overview.ghost_live > 0 }">
        <b>{{ overview.ghost_live }}</b><span>活态幽灵 / 搁置 {{ overview.ghost_holds }}</span>
      </div>
    </div>
    <section v-for="cab in bay" :key="cab.id" class="cabinet panel">
      <h2>{{ cab.label }} <small>{{ cab.id }} · {{ cab.site }}</small></h2>
      <div class="grid">
        <div
          v-for="cell in cab.cells"
          :key="cell.id"
          class="cell"
          :class="[cell.status, { ghost: cell.ghost }]"
        >
          <div>
            <div class="code">{{ cell.code }}</div>
            <div class="meta">{{ cell.size_code }} · {{ cell.status === 'occupied' ? '占用' : '空闲' }}</div>
          </div>
          <div v-if="cell.ghost" class="ghost-tag">
            幽灵 · {{ cell.ghost.kind === 'open_occupancy_but_cell_free' ? '有账无格态' : '有格态无账' }}
          </div>
          <div class="meta" v-else-if="cell.status === 'occupied' && cell.occupancy?.tracking_no">{{ cell.occupancy.tracking_no }}</div>
          <span class="dot" />
        </div>
      </div>
    </section>
  </div>
</template>
