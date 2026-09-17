<template>
  <div class="service-panel">
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>服务状态</h2>
          <p>当前部署的运行环境、数据库与算法服务连通性。</p>
        </div>
        <div class="heading-actions">
          <span class="status-chip" :class="databaseReady ? 'is-ok' : 'is-danger'">
            数据库 {{ databaseReady ? '正常' : '不可用' }}
          </span>
          <span class="status-chip" :class="meshReady ? 'is-ok' : 'is-warning'">
            算法服务 {{ meshReady ? '正常' : '不可用' }}
          </span>
          <button class="action" type="button" :disabled="loading" @click="emit('refresh')">
            {{ loading ? '刷新中...' : '刷新' }}
          </button>
        </div>
      </div>

      <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

      <dl v-if="info" class="facts">
        <div class="fact"><dt>工作区</dt><dd>{{ info.workspace.name }}</dd></div>
        <div class="fact"><dt>服务名称</dt><dd>{{ info.service }}</dd></div>
        <div class="fact"><dt>运行环境</dt><dd>{{ info.environment }}</dd></div>
        <div class="fact"><dt>数据库类型</dt><dd>{{ info.database }}</dd></div>
        <div class="fact"><dt>服务器时间</dt><dd>{{ formatDateTime(info.serverTime) }}</dd></div>
        <div class="fact"><dt>启动时间</dt><dd>{{ formatDateTime(info.startedAt) }}</dd></div>
        <div class="fact"><dt>已运行</dt><dd>{{ formatUptime(info.uptimeSeconds) }}</dd></div>
        <div class="fact"><dt>存储目录</dt><dd>{{ info.dataDir || (info.storageConfigured ? '已配置（仅管理员可见路径）' : '未配置') }}</dd></div>
        <div class="fact"><dt>处理并发</dt><dd>{{ info.assets.workerCount }} 个工作进程</dd></div>
        <div class="fact"><dt>算法服务地址</dt><dd>{{ info.meshService.url || '未配置' }}</dd></div>
        <div class="fact"><dt>算法服务延迟</dt><dd>{{ info.meshService.status === 'ok' ? `${info.meshService.latencyMs} ms` : '不可用' }}</dd></div>
        <div class="fact"><dt>当前角色</dt><dd>{{ info.roleLabel }}</dd></div>
      </dl>

      <div v-else-if="!loading" class="empty-state">
        <strong>暂无服务信息</strong>
        <span>请稍后重试，或检查后端服务是否已启动。</span>
      </div>
    </section>

    <section v-if="info" class="panel">
      <div class="panel-heading">
        <div>
          <h2>用量统计</h2>
          <p>工作区范围内的项目、成员与资产总量。</p>
        </div>
      </div>

      <div class="stat-grid">
        <article v-for="stat in stats" :key="stat.label" class="stat">
          <span>{{ stat.label }}</span>
          <strong>{{ stat.value }}</strong>
          <small>{{ stat.hint }}</small>
        </article>
      </div>

      <dl class="facts">
        <div class="fact"><dt>源文件占用</dt><dd>{{ formatBytes(info.storage.sourceBytes) }}</dd></div>
        <div class="fact"><dt>衍生产物占用</dt><dd>{{ formatBytes(info.storage.derivativeBytes) }}</dd></div>
        <div class="fact"><dt>存储合计</dt><dd>{{ formatBytes(info.storage.totalBytes) }}</dd></div>
        <div class="fact"><dt>进行中上传</dt><dd>{{ formatBytes(info.storage.pendingUploadBytes) }}</dd></div>
        <div class="fact"><dt>我的项目 / 资产</dt><dd>{{ info.projectCount }} / {{ info.assetCount }}</dd></div>
        <div class="fact"><dt>我的对齐记录</dt><dd>{{ info.alignmentCount.toLocaleString('zh-CN') }}</dd></div>
      </dl>

      <div class="breakdown">
        <div class="breakdown-group">
          <h3>按资产类型</h3>
          <ul v-if="info.assets.byType.length" class="chip-list">
            <li v-for="item in info.assets.byType" :key="`type-${item.type}`">
              <span class="status-chip">{{ assetTypeLabel(item.type) }} · {{ item.total.toLocaleString('zh-CN') }}</span>
            </li>
          </ul>
          <p v-else class="muted">工作区暂无资产。</p>
        </div>
        <div class="breakdown-group">
          <h3>按处理状态</h3>
          <ul v-if="info.assets.byStatus.length" class="chip-list">
            <li v-for="item in info.assets.byStatus" :key="`status-${item.type}`">
              <span class="status-chip" :class="statusClass(item.type)">
                {{ assetStatusLabel(item.type) }} · {{ item.total.toLocaleString('zh-CN') }}
              </span>
            </li>
          </ul>
          <p v-else class="muted">工作区暂无资产。</p>
        </div>
      </div>
    </section>

    <section v-if="info" class="panel">
      <div class="panel-heading">
        <div>
          <h2>权限与能力</h2>
          <p>当前账号在工作区内的授权范围，以及已开放的系统管理能力。</p>
        </div>
      </div>

      <div class="breakdown">
        <div class="breakdown-group">
          <h3>我的权限</h3>
          <ul class="chip-list">
            <li v-for="permission in info.member.permissions" :key="permission">
              <span class="status-chip">{{ permissionLabel(permission) }}</span>
            </li>
          </ul>
        </div>
        <div class="breakdown-group">
          <h3>可用模块</h3>
          <ul class="capability-list">
            <li v-for="capability in info.capabilities" :key="capability.key">
              <strong>{{ capability.label }}</strong>
              <span>{{ capability.description }}</span>
            </li>
          </ul>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatFileSize } from '@cloudbim/viewer-core'
import type { SystemInfo } from '@/api/backend-system'

const props = defineProps<{ info: SystemInfo | null; loading: boolean; error?: string }>()
const emit = defineEmits<{ refresh: [] }>()

const databaseReady = computed(() => props.info?.databaseStatus === 'ready')
const meshReady = computed(() => props.info?.meshService.status === 'ok')

const stats = computed(() => {
  const workspace = props.info?.workspace

  if (!workspace) {
    return []
  }

  return [
    { label: '成员总数', value: workspace.memberCount.toLocaleString('zh-CN'), hint: `启用管理员 ${workspace.adminCount} 名` },
    { label: '项目总数', value: workspace.projectCount.toLocaleString('zh-CN'), hint: '工作区全部项目' },
    { label: '资产总数', value: workspace.assetCount.toLocaleString('zh-CN'), hint: '模型、图纸与点云' },
    { label: '对齐记录', value: workspace.alignmentCount.toLocaleString('zh-CN'), hint: `量测记录 ${workspace.measurementCount} 条` },
    { label: '自助注册', value: workspace.allowRegistration ? '已开启' : '已关闭', hint: workspace.allowRegistration ? '可使用注册码注册' : '仅管理员开通账号' },
  ]
})

const ASSET_TYPE_LABELS: Record<string, string> = {
  bim: '设计模型',
  cad: 'CAD 图纸',
  pointcloud: '扫描点云',
}

const ASSET_STATUS_LABELS: Record<string, string> = {
  uploading: '上传中',
  queued: '排队中',
  processing: '处理中',
  ready: '已就绪',
  failed: '失败',
}

const PERMISSION_LABELS: Record<string, string> = {
  'workspace:read': '查看工作区信息',
  'member:manage': '管理成员与角色',
  'asset:manage': '上传与管理资产',
}

function assetTypeLabel(type: string) {
  return ASSET_TYPE_LABELS[type] || type
}

function assetStatusLabel(status: string) {
  return ASSET_STATUS_LABELS[status] || status
}

function statusClass(status: string) {
  if (status === 'ready') {
    return 'is-ok'
  }

  if (status === 'failed') {
    return 'is-danger'
  }

  return 'is-warning'
}

function permissionLabel(permission: string) {
  return PERMISSION_LABELS[permission] || permission
}

function formatBytes(bytes: number) {
  return formatFileSize(bytes)
}

function formatDateTime(value?: string) {
  if (!value) {
    return '-'
  }

  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString('zh-CN', { hour12: false })
}

function formatUptime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return '刚刚启动'
  }

  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)

  if (days > 0) {
    return `${days} 天 ${hours} 小时`
  }

  if (hours > 0) {
    return `${hours} 小时 ${minutes} 分钟`
  }

  return `${Math.max(minutes, 1)} 分钟`
}
</script>

<style scoped lang="scss">
@use '@/styles/system-panels' as panels;
@use '@cloudbim/viewer-core/styles/workspace-controls.scss' as controls;

.service-panel { display: flex; flex-direction: column; gap: var(--workspace-gap); min-width: 0; }
.panel { @include panels.panel; }
.panel-heading { @include panels.panel-heading; }
.facts { @include panels.facts; }
.status-chip { @include panels.status-chip; }
.empty-state { @include panels.empty-state; }
.panel-error { @include panels.notice; border-left-color: var(--color-danger); color: var(--color-danger); }
.action { @include controls.action; }

.heading-actions { display: flex; align-items: center; flex-wrap: wrap; gap: var(--spacing-sm); }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: var(--spacing-md); }
.stat {
  display: flex; flex-direction: column; gap: var(--spacing-xs);
  padding: var(--spacing-compact);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-sm);
  background: var(--bg-control);
}
.stat span { color: var(--text-tertiary); font-size: var(--font-size-xs); }
.stat strong { color: var(--text-primary); font-size: var(--font-size-xl); font-weight: 650; font-variant-numeric: tabular-nums; }
.stat small { color: var(--text-tertiary); font-size: var(--font-size-xs); }

.breakdown { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: var(--spacing-lg); }
.breakdown-group h3 { margin: 0 0 var(--spacing-sm); color: var(--text-secondary); font-size: var(--font-size-sm); font-weight: 600; }
.chip-list { display: flex; flex-wrap: wrap; gap: var(--spacing-sm); margin: 0; padding: 0; list-style: none; }
.capability-list { display: flex; flex-direction: column; gap: var(--spacing-sm); margin: 0; padding: 0; list-style: none; }
.capability-list li { display: flex; flex-direction: column; gap: 2px; }
.capability-list strong { color: var(--text-primary); font-size: var(--font-size-sm); font-weight: 600; }
.capability-list span { color: var(--text-tertiary); font-size: var(--font-size-xs); }
.muted { margin: 0; color: var(--text-tertiary); font-size: var(--font-size-sm); }
</style>
