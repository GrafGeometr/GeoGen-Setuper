import java.util.Locale

data class Finding(
    val id: String, val status: String, val theorem: String, val file: String, val detail: String,
    val beauty: Double? = null, val topic: String = theorem.substringBefore(':').trim().ifEmpty { "Unknown" }
)

val topicLabels = linkedMapOf(
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
    return Finding(cols[0], cols[1], cols[2], cols[3], cols[4], score, topic)
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
