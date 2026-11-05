
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Canvas
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import org.jetbrains.skia.Font
import org.jetbrains.skia.Data
import org.jetbrains.skia.Typeface
import org.jetbrains.skia.Paint
import org.jetbrains.skia.TextLine

private val suiteTypeface by lazy {
    val bytes = object {}.javaClass.getResourceAsStream("/DejaVuSans.ttf")!!.use { it.readBytes() }
    Typeface.makeFromData(Data.makeFromBytes(bytes))
}

object TextDrawer : com.ibis.geometry.common.TextDrawer {
    override fun text(canvas: Canvas, pos: Offset, text: List<String>, color: Color) {
        val paint = Paint()
        paint.color = color.toArgb()
        val font = Font(suiteTypeface, 40f)
        val subscript = Font(suiteTypeface, 30f)
        var current = pos.x
        text.forEachIndexed { index, str ->
            val line = TextLine.make(str, if (index % 2 == 0) font else subscript)
            canvas.nativeCanvas.drawTextLine(line, current + 12f, pos.y + if (index % 2 == 0) 12f else 24f, paint)
            current += line.width
        }
    }
}
