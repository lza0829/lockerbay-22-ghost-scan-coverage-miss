import { createRouter, createWebHistory } from 'vue-router'
import Bay from './pages/Bay.vue'
import Ingest from './pages/Ingest.vue'
import Reclaim from './pages/Reclaim.vue'
import Proofs from './pages/Proofs.vue'
import Ghost from './pages/Ghost.vue'
import Settings from './pages/Settings.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Bay },
    { path: '/ingest', component: Ingest },
    { path: '/reclaim', component: Reclaim },
    { path: '/proofs', component: Proofs },
    { path: '/ghost', component: Ghost },
    { path: '/settings', component: Settings },
  ],
})
