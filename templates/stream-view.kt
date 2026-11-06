import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.awt.ComposeWindow
import com.ibis.geometry.common.App
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.coroutines.launch

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
    var topic by remember(job) { mutableStateOf<String?>(null) }
    var topicMenu by remember(job) { mutableStateOf(false) }
    var rowOrder by remember(job) { mutableStateOf(emptyList<String>()) }
    var topicOrder by remember(job) { mutableStateOf(emptyList<String>()) }
    var collapsed by remember(job) { mutableStateOf(emptySet<String>()) }
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    fun eligible(rows: List<Finding>) = rows.filter { (!readyOnly || it.file.isNotBlank()) && (topic == null || it.topic == topic) }
    LaunchedEffect(job, readyOnly, topic) {
        while (true) {
            try {
                val snapshot = withContext(Dispatchers.IO) {
                    val rows = File(job, "results.tsv").takeIf { it.isFile }?.readLines(Charsets.UTF_8)?.mapNotNull(::parseFinding) ?: emptyList()
                    val status = File(job, "stream-status.txt").takeIf { it.isFile }?.readLines(Charsets.UTF_8) ?: emptyList()
                    Triple(rows, status.firstOrNull() ?: "Ожидание запуска", status.getOrNull(1) ?: "")
                }
                findings = snapshot.first
                val visible = eligible(snapshot.first)
                rowOrder = mergeFindingOrder(rowOrder, visible)
                topicOrder = topicOrder + visible.map { it.topic }.distinct().filter { it !in topicOrder }
                phase = labels[snapshot.second] ?: snapshot.second
                counts = snapshot.third.split(" · ").joinToString(" · ") { item ->
                    val parts = item.split(": ", limit = 2)
                    (labels[parts[0]] ?: parts[0]) + if (parts.size == 2) ": " + parts[1] else ""
                }
                readError = ""
                if (selected == null) selected = visible.firstOrNull { it.file.isNotBlank() }?.id
            } catch (error: Exception) {
                readError = "Не удалось прочитать результаты: ${error.message}"
            }
            delay(500)
        }
    }
    val visible = eligible(findings)
    val byId = visible.associateBy { it.id }
    val ordered = rowOrder.mapNotNull { byId[it] }
    fun resetScroll() { scope.launch { listState.scrollToItem(0) } }
    Row(Modifier.fillMaxSize()) {
        Column(Modifier.width(380.dp).fillMaxHeight().background(Color(0xfff2f3f5)).padding(10.dp)) {
            // Constant height: growing counters and status labels cannot move the viewport.
            Column(Modifier.fillMaxWidth().height(104.dp)) {
                Text("Результаты поиска", style = MaterialTheme.typography.h6, maxLines = 1)
                Text(phase, style = MaterialTheme.typography.subtitle2, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text("Находок: ${findings.size} · показано: ${visible.size}", style = MaterialTheme.typography.caption)
                Text(counts, style = MaterialTheme.typography.caption, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            Box(Modifier.fillMaxWidth().height(18.dp)) {
                if (readError.isNotEmpty()) Text(readError, color = MaterialTheme.colors.error, maxLines = 1, style = MaterialTheme.typography.caption)
            }
            Row(Modifier.height(36.dp), verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                Checkbox(readyOnly, { readyOnly = it; rowOrder = emptyList(); selected = null; resetScroll() })
                Text("Только готовые")
            }
            Box(Modifier.fillMaxWidth().height(36.dp)) {
                TextButton({ topicMenu = true }, Modifier.fillMaxWidth()) {
                    Text("Тема: " + (topic?.let(::topicLabel) ?: "Все темы"), maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                DropdownMenu(topicMenu, { topicMenu = false }) {
                    DropdownMenuItem({ topic = null; topicMenu = false; rowOrder = emptyList(); selected = null; resetScroll() }) { Text("Все темы") }
                    findings.map { it.topic }.distinct().sortedBy(::topicLabel).forEach { kind ->
                        DropdownMenuItem({ topic = kind; topicMenu = false; rowOrder = emptyList(); selected = null; resetScroll() }) { Text(topicLabel(kind)) }
                    }
                }
            }
            Row(Modifier.fillMaxWidth().height(36.dp)) {
                TextButton({ rowOrder = sortByBeauty(visible); resetScroll() }) { Text("По красоте ↓") }
                TextButton({ rowOrder = visible.map { it.id }; resetScroll() }) { Text("По поступлению") }
            }
            Text("Новые задачи — в конце темы. Сортировка — по кнопке.", Modifier.height(32.dp), style = MaterialTheme.typography.caption, maxLines = 2)
            LazyColumn(Modifier.weight(1f), state = listState) {
                if (visible.isEmpty()) item(key = "empty") {
                    Text(if (readyOnly) "Готовых задач пока нет. Снимите фильтр, чтобы увидеть очередь и ошибки." else "Ожидаем находки GeoGen…", Modifier.padding(8.dp))
                }
                topicOrder.forEach { kind ->
                    val group = ordered.filter { it.topic == kind }
                    if (group.isNotEmpty()) {
                        item(key = "topic:$kind") {
                            Row(Modifier.fillMaxWidth().height(36.dp).background(Color(0xffe2e5eb))
                                .clickable { collapsed = if (kind in collapsed) collapsed - kind else collapsed + kind }.padding(horizontal = 8.dp),
                                verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                                Text((if (kind in collapsed) "▸ " else "▾ ") + topicLabel(kind) + " (${group.size})",
                                    maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.subtitle2)
                            }
                        }
                        if (kind !in collapsed) items(group, key = { "finding:" + it.id }) { row ->
                            Column(Modifier.fillMaxWidth().height(96.dp).background(if (row.id == selected) Color(0xffdce8ff) else Color.Transparent)
                                .clickable { selected = row.id }.padding(8.dp)) {
                                Text(row.theorem, Modifier.height(40.dp), style = MaterialTheme.typography.body2, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                Text("Красота GeoGen: ${beautyLabel(row.beauty)}", style = MaterialTheme.typography.caption, maxLines = 1)
                                Text(labels[row.status] ?: row.status,
                                    color = if (row.status == "error" || row.status == "timeout") MaterialTheme.colors.error else Color.DarkGray,
                                    style = MaterialTheme.typography.caption, maxLines = 1)
                            }
                            Divider()
                        }
                    }
                }
            }
        }
        Column(Modifier.weight(1f).fillMaxHeight()) {
            val row = findings.firstOrNull { it.id == selected }
            if (row != null) Column(Modifier.fillMaxWidth().height(60.dp).background(Color(0xfff2f3f5)).padding(8.dp)) {
                Text(topicLabel(row.topic), maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.subtitle2)
                Text("Красота GeoGen: ${beautyLabel(row.beauty)} · ${labels[row.status] ?: row.status}", style = MaterialTheme.typography.body2)
            }
            Box(Modifier.weight(1f).fillMaxWidth()) {
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
}
