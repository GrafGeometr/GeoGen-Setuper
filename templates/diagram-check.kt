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
    checkDiagramPremise(file, context, input)
    null
} catch(error: Exception) { error.message ?: error.javaClass.simpleName }

fun checkGeoInputs(path: File) {
    val files = if(path.isDirectory) path.listFiles()?.filter { it.extension == "geo" }?.sortedBy { it.name }.orEmpty() else listOf(path)
    val failures = files.mapNotNull { file -> diagramProblem(file)?.let { message ->
        "{\"file\":" + jsonText(file.name) + ",\"message\":" + jsonText(message) + "}"
    }}
    println("{\"status\":\"complete\",\"checked\":" + files.size + ",\"failures\":[" + failures.joinToString(",") + "]}")
}

private fun checkDiagramPremise(file: File, context: ParseContext, input: ReactiveInput) {
    val premises = file.readLines().filter { it.startsWith("!Assume:") }
    if (premises.isEmpty()) return
    check(premises.size == 1) { "Expected exactly one additional condition" }
    val premise = premises.single().removePrefix("!Assume:").trim().removeSuffix(" - assumption")
    val kind = premise.substringBefore(':')
    val args = Regex("\\[[^\\]]+\\]|\\w+").findAll(premise.substringAfter(':')).map { it.value }.toList()
    fun point(name: String): Pair<Double, Double> {
        val p = (context.names[name] ?: error("Unknown premise point $name"))(input) as Complex
        return p.re.toDouble() to p.im.toDouble()
    }
    fun diff(a: Pair<Double,Double>, b: Pair<Double,Double>) = a.first-b.first to a.second-b.second
    fun norm(a: Pair<Double,Double>) = a.first*a.first+a.second*a.second
    fun cross(a: Pair<Double,Double>, b: Pair<Double,Double>) = a.first*b.second-a.second*b.first
    fun dot(a: Pair<Double,Double>, b: Pair<Double,Double>) = a.first*b.first+a.second*b.second
    fun direction(token: String): Pair<Double,Double> {
        if (token.startsWith("[")) {
            val pair = token.removePrefix("[").removeSuffix("]").split(',').map { it.trim() }
            return diff(point(pair[1]),point(pair[0]))
        }
        val line = (context.names[token] ?: error("Unknown premise line $token"))(input) as Line
        return -line.coef.im.toDouble() to line.coef.re.toDouble()
    }
    val residual = when (kind) {
        "CollinearPoints" -> {
            val u=diff(point(args[1]),point(args[0]));val v=diff(point(args[2]),point(args[0]))
            cross(u,v)/kotlin.math.sqrt(norm(u)*norm(v))
        }
        "ParallelLines", "PerpendicularLines" -> {
            val u=direction(args[0]);val v=direction(args[1])
            (if (kind=="ParallelLines") cross(u,v) else dot(u,v))/kotlin.math.sqrt(norm(u)*norm(v))
        }
        "EqualLineSegments" -> {
            val u=norm(diff(point(args[0]),point(args[1])));val v=norm(diff(point(args[2]),point(args[3])))
            (u-v)/(u+v)
        }
        "ConcyclicPoints" -> {
            val a=point(args[0]);val u=diff(point(args[1]),a);val v=diff(point(args[2]),a);val w=diff(point(args[3]),a)
            val det=2*cross(u,v)
            check(kotlin.math.abs(det)>1e-10) { "Undefined premise circle" }
            val o=(norm(u)*v.second-norm(v)*u.second)/det to (u.first*norm(v)-v.first*norm(u))/det
            val radius=norm(o);val distance=norm(diff(w,o))
            (distance-radius)/(distance+radius)
        }
        else -> error("Unsupported premise type $kind")
    }
    // Geometry uses Float coordinates; this is a drawing check, not the native truth certificate.
    check(residual.isFinite() && kotlin.math.abs(residual)<0.002) { "Additional condition fails in Geometry: $premise; residual=$residual" }
}
