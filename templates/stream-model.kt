import java.util.Locale

data class Finding(
    val id: String, val status: String, val theorem: String, val file: String, val detail: String,
    val beauty: Double? = null, val topic: String = theorem.substringBefore(':').trim().ifEmpty { "Unknown" },
    val quality: String = "unverified", val qualityScore: Double? = null, val conditionFile: String = ""
)

val topicLabels = linkedMapOf(
    "LengthRatio2" to "Отношение длин 2:1",
    "LengthRatio3" to "Отношение длин 3:1",
    "SquaredLengthRatio2" to "Отношение квадратов длин 2:1",
    "EqualAngles" to "Равенство направленных углов",
    "CollinearPoints" to "Коллинеарность",
    "ConcyclicPoints" to "Точки на одной окружности",
    "ConcurrentLines" to "Пересечение прямых в одной точке",
    "ParallelLines" to "Параллельность",
    "PerpendicularLines" to "Перпендикулярность",
    "EqualLineSegments" to "Равенство отрезков",
    "EqualObjects" to "Совпадение объектов",
    "LineTangentToCircle" to "Касание прямой и окружности",
    "TangentCircles" to "Касание окружностей",
    "Incidence" to "Принадлежность точки"
)
fun topicLabel(topic: String) = topicLabels[topic] ?: if (topic == "Unknown") "Другие утверждения" else topic
fun beautyLabel(value: Double?) = value?.let { String.format(Locale.ROOT, "%.2f", it) } ?: "нет оценки"

fun parseFinding(line: String): Finding? {
    val cols = line.split('\t')
    if (cols.size < 5) return null
    val score = cols.getOrNull(5)?.toDoubleOrNull()?.takeIf { it.isFinite() }
    val topic = cols.getOrNull(6)?.takeIf { it.isNotBlank() }
        ?: cols[2].substringBefore(':').trim().ifEmpty { "Unknown" }
    return Finding(cols[0], cols[1], cols[2], cols[3], cols[4], score, topic, cols.getOrNull(7) ?: "unverified", cols.getOrNull(8)?.toDoubleOrNull()?.takeIf { it.isFinite() }, cols.getOrNull(9) ?: "")
}

// Append newly visible rows; completion timestamps and producer ordering must never
// move rows the reader has already seen. Removal only happens on explicit filters.
fun mergeFindingOrder(previous: List<String>, current: List<Finding>): List<String> {
    val present = current.map { it.id }.toHashSet()
    val kept = previous.filter { it in present }
    val known = kept.toHashSet()
    return kept + current.map { it.id }.filter { known.add(it) }
}
fun sortByBeauty(rows: List<Finding>): List<String> = rows.sortedWith(
    compareByDescending<Finding> { it.beauty ?: Double.NEGATIVE_INFINITY }.thenBy { it.id }
).map { it.id }

fun qualityLabel(value: String) = when(value) {
    "sampled-valid" -> "Проверено на точных пробах"
    "review" -> "Нужна проверка условий"
    "standard" -> "Стандартное доказательство"
    else -> "Истинность отдельно не проверялась"
}

val findingSections = linkedMapOf(
    "ready" to "Готовые", "rejected" to "Отбракованные", "timeout" to "Таймауты",
    "review" to "Нужна проверка", "other" to "Очередь и ошибки", "all" to "Все находки"
)
fun inFindingSection(row: Finding, section: String): Boolean = when (section) {
    "ready" -> row.status in setOf("ready", "unchecked")
    "rejected" -> row.status in setOf("rejected", "standard", "duplicate")
    "timeout" -> row.status == "timeout"
    "review" -> row.status == "review"
    "other" -> row.status !in setOf("ready", "unchecked", "rejected", "standard", "duplicate", "timeout", "review")
    "all" -> true
    else -> false
}

fun loadFindingCondition(job: java.io.File, row: Finding): String {
    val root = job.canonicalFile
    val candidates = listOf(row.conditionFile, "tasks/${row.id}/check.geo", "tasks/${row.id}/theorem.txt")
    for (relative in candidates.filter { it.isNotBlank() }) {
        val file = java.io.File(root, relative).canonicalFile
        if (!file.toPath().startsWith(root.toPath()) || !file.isFile) continue
        // New manifests point only into conditions; legacy task files stay inside tasks.
        if (!file.toPath().startsWith(java.io.File(root, "conditions").toPath()) &&
            !file.toPath().startsWith(java.io.File(root, "tasks").toPath())) continue
        return file.readText(Charsets.UTF_8).lineSequence().joinToString("\n") { it.removePrefix("!") }
    }
    return "Полное условие пока недоступно. Для старого завершённого запуска откройте его командой watch нового комплекта.\n\n" + row.theorem
}
