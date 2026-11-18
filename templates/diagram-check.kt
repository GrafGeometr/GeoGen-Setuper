import com.ibis.geometry.common.*
import java.io.File

private fun jsonText(text: String): String = "\"" + buildString {
    text.forEach { c -> when(c) {
        '\\' -> append("\\\\")
        '"' -> append("\\\"")
        '\n' -> append("\\n")
        '\r' -> append("\\r")
        '\t' -> append("\\t")
        else -> if(c.code < 32) append("\\u%04x".format(c.code)) else append(c)
    }}
} + "\""

private fun diagramProblem(file: File): String? = try {
    val context = ParseContext(mutableMapOf(), mutableMapOf(), null)
    val input = ReactiveInput(0, emptyList())
    file.readLines().filter { it.isNotBlank() && !it.trimStart().startsWith("!") }.forEach {
        context.parseLine(it)?.invoke(input)
    }
    context.names.values.forEach { value -> when(val shape = value(input)) {
        is Complex -> check(shape.re.isFinite() && shape.im.isFinite()) { "Nonfinite point" }
        is Circle -> check(shape.radiusSqr.isFinite() && shape.radiusSqr > 0) { "Invalid circle" }
        is Line -> check(shape.free.isFinite() && shape.coef.norm > 0) { "Invalid line" }
        else -> Unit
    }}
    null
} catch(error: Exception) { error.message ?: error.javaClass.simpleName }

fun checkGeoInputs(path: File) {
    val files = if(path.isDirectory) path.listFiles()?.filter { it.extension == "geo" }?.sortedBy { it.name }.orEmpty() else listOf(path)
    val failures = files.mapNotNull { file -> diagramProblem(file)?.let { message ->
        "{\"file\":" + jsonText(file.name) + ",\"message\":" + jsonText(message) + "}"
    }}
    println("{\"status\":\"complete\",\"checked\":" + files.size + ",\"failures\":[" + failures.joinToString(",") + "]}")
}
