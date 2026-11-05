import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.awt.ComposeWindow
import com.ibis.geometry.common.App
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

private data class Finding(val id: String, val status: String, val theorem: String, val file: String, val detail: String)
private val labels = mapOf("queued" to "Ожидает", "processing" to "Проверяется", "ready" to "Готово",
    "unchecked" to "Без проверки", "rejected" to "Отбраковано", "timeout" to "Таймаут",
    "error" to "Ошибка", "duplicate" to "Повтор", "searching" to "Идёт поиск",
    "draining" to "Обработка очереди", "finished" to "Завершено", "stopped" to "Остановлено", "failed" to "Сбой поиска")

@Composable
fun StreamView(job: File, globalFile: File, window: ComposeWindow, fullscreen: Boolean) {
    var findings by remember(job) { mutableStateOf(emptyList<Finding>()) }
    var phase by remember(job) { mutableStateOf("Ожидание запуска") }
    var counts by remember(job) { mutableStateOf("") }
    var readError by remember(job) { mutableStateOf("") }
    var selected by remember(job) { mutableStateOf<String?>(null) }
    var readyOnly by remember(job) { mutableStateOf(true) }
    LaunchedEffect(job) {
        while (true) {
            try {
                val snapshot = withContext(Dispatchers.IO) {
                    val rows = File(job, "results.tsv").takeIf { it.isFile }?.readLines(Charsets.UTF_8)?.mapNotNull { line ->
                        val cols = line.split('\t', limit = 5)
                        if (cols.size == 5) Finding(cols[0], cols[1], cols[2], cols[3], cols[4]) else null
                    } ?: emptyList()
                    val status = File(job, "stream-status.txt").takeIf { it.isFile }?.readLines(Charsets.UTF_8) ?: emptyList()
                    Triple(rows, status.firstOrNull() ?: "Ожидание запуска", status.getOrNull(1) ?: "")
                }
                findings = snapshot.first
                phase = labels[snapshot.second] ?: snapshot.second
                counts = snapshot.third.split(" · ").joinToString(" · ") { item ->
                    val parts = item.split(": ", limit = 2)
                    (labels[parts[0]] ?: parts[0]) + if (parts.size == 2) ": " + parts[1] else ""
                }
                readError = ""
                // New arrivals never replace a drawing already selected by the user.
                if (selected == null) selected = findings.firstOrNull { it.file.isNotBlank() }?.id
            } catch (error: Exception) {
                readError = "Не удалось прочитать результаты: ${error.message}"
            }
            delay(500)
        }
    }
    Row(Modifier.fillMaxSize()) {
        Column(Modifier.width(340.dp).fillMaxHeight().background(Color(0xfff2f3f5)).padding(12.dp)) {
            Text("Результаты поиска", style = MaterialTheme.typography.h6)
            Text(phase, style = MaterialTheme.typography.subtitle1)
            Text("Находок: ${findings.size}", style = MaterialTheme.typography.caption)
            if (counts.isNotEmpty()) Text(counts, style = MaterialTheme.typography.caption)
            if (readError.isNotEmpty()) Text(readError, color = MaterialTheme.colors.error)
            Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                Checkbox(readyOnly, { readyOnly = it })
                Text("Только готовые")
            }
            val visible = if (readyOnly) findings.filter { it.file.isNotBlank() } else findings
            if (visible.isEmpty()) Text(if (readyOnly) "Готовых задач пока нет. Снимите фильтр, чтобы увидеть очередь и ошибки." else "Ожидаем находки GeoGen…")
            LazyColumn(Modifier.weight(1f)) {
                items(visible, key = { it.id }) { row ->
                    Column(Modifier.fillMaxWidth().background(if (row.id == selected) Color(0xffdce8ff) else Color.Transparent)
                        .clickable { selected = row.id }.padding(8.dp)) {
                        Text(row.theorem, style = MaterialTheme.typography.body2)
                        Text(labels[row.status] ?: row.status,
                            color = if (row.status == "error" || row.status == "timeout") MaterialTheme.colors.error else Color.DarkGray,
                            style = MaterialTheme.typography.caption)
                    }
                    Divider()
                }
            }
        }
        Box(Modifier.weight(1f).fillMaxHeight()) {
            val row = findings.firstOrNull { it.id == selected }
            if (row == null) {
                Text("Выберите задачу слева. Новые результаты появятся автоматически.", Modifier.padding(24.dp))
            } else if (row.file.isBlank()) {
                Column(Modifier.padding(24.dp)) {
                    Text(row.theorem, style = MaterialTheme.typography.h6)
                    Text(labels[row.status] ?: row.status)
                    if (row.detail.isNotEmpty()) Text(row.detail)
                }
            } else {
                val geoDir = File(job, "geo").canonicalFile
                val file = File(geoDir, row.file).canonicalFile
                if (file.parentFile != geoDir || !file.isFile) {
                    Text("Файл чертежа пока недоступен", Modifier.padding(24.dp))
                } else key(row.id) {
                    val input = remember { mutableStateOf(file) }
                    App(globalFile, FileManager(window, input), TextDrawer, fullscreen)
                }
            }
        }
    }
}
