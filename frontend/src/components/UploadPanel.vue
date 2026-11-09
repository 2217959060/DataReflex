<script setup lang="ts">
import { computed, ref } from "vue";
import { importDatasets } from "../api/client";

const emit = defineEmits<{
  imported: [datasetId: string];
}>();

const fileInput = ref<HTMLInputElement | null>(null);
const files = ref<File[]>([]);
const datasetId = ref("");
const busy = ref(false);
const message = ref("");
const error = ref("");

const pendingImport = computed(
  () => files.value.length > 0 && !datasetId.value && !busy.value,
);

const fileLabel = computed(() => {
  if (!files.value.length) return "选择文件";
  return `已选 ${files.value.length} 个文件`;
});

function syncFilesFromInput() {
  const input = fileInput.value;
  files.value = input?.files ? Array.from(input.files) : [];
  if (files.value.length) {
    datasetId.value = "";
    message.value = "";
  }
}

function onPick() {
  error.value = "";
  syncFilesFromInput();
}

async function onUpload() {
  error.value = "";
  message.value = "";
  syncFilesFromInput();
  if (!files.value.length) {
    error.value = "请先选择 CSV/Excel 文件";
    return;
  }
  busy.value = true;
  try {
    const resp = await importDatasets(files.value);
    datasetId.value = resp.dataset_id;
    message.value = `导入成功，共 ${resp.tables.length} 张表`;
    emit("imported", resp.dataset_id);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="panel">
    <h2>1. 上传数据</h2>
    <p class="hint">支持 CSV / Excel，建议上传 orders / returns / users。</p>

    <div class="upload-stack">
      <div class="upload-row">
        <label class="file-btn">
          <input
            ref="fileInput"
            type="file"
            multiple
            accept=".csv,.xlsx,.xls,.xlsm"
            @change="onPick"
          />
          {{ fileLabel }}
        </label>
        <span v-if="pendingImport" class="err inline-status">未导入</span>
        <span v-else-if="datasetId" class="ok inline-status">已导入</span>
        <span v-else class="muted inline-status">尚未选择文件</span>
      </div>

      <div class="upload-row">
        <button
          type="button"
          class="action-btn"
          :disabled="busy || !files.length"
          @click="onUpload"
        >
          {{ busy ? "上传中…" : "上传并导入" }}
        </button>
        <span v-if="datasetId" class="pill">dataset_id: {{ datasetId }}</span>
        <span v-else-if="files.length" class="muted">请点击导入完成上传</span>
      </div>
    </div>

    <p v-if="message" class="ok">{{ message }}</p>
    <p v-if="error" class="err">{{ error }}</p>
  </section>
</template>
