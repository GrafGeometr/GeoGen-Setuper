import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.material.MaterialTheme
import androidx.compose.material.Typography
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.platform.Font
import androidx.compose.ui.unit.dp
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.type
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.window.Window
import androidx.compose.ui.window.WindowPlacement
import androidx.compose.ui.window.application
import androidx.compose.ui.window.rememberWindowState
import com.ibis.geometry.common.App
import com.ibis.geometry.common.theme.GeometryTheme
import java.io.File

@OptIn(ExperimentalComposeUiApi::class)
fun main(args: Array<String>) = application {
    val dataHome = System.getenv("XDG_DATA_HOME")?.takeIf { it.isNotBlank() }
        ?: File(System.getProperty("user.home"), ".local/share").path
    val dataDir = File(dataHome, "geometry-desktop").apply { mkdirs() }
    val watchDir = if (args.firstOrNull() == "--watch" && args.size >= 2) File(args[1]) else null
    val inputFile = remember {
        mutableStateOf(if (watchDir == null && args.isNotEmpty()) File(args[0]) else File(dataDir, "input.geo"))
    }
    val globalFile = if (watchDir == null && args.size >= 2) File(args[1]) else File(dataDir, "global.geo")
    inputFile.value.createNewFile()
    globalFile.createNewFile()
    val state = rememberWindowState(width = if (watchDir != null) 1200.dp else 800.dp, height = 800.dp)
    Window(
        onCloseRequest = ::exitApplication,
        state = state,
        title = "Geometry Desktop",
        icon = painterResource("icon.png"),
        onPreviewKeyEvent = {
            if (it.key == Key.F11 && it.type == KeyEventType.KeyUp) {
                state.placement = if (state.placement == WindowPlacement.Fullscreen)
                    WindowPlacement.Floating else WindowPlacement.Fullscreen
                true
            } else false
        }
    ) {
        GeometryTheme {
            val font = remember { FontFamily(Font("DejaVuSans.ttf")) }
            MaterialTheme(typography = Typography(defaultFontFamily = font)) {
            if (watchDir != null) StreamView(watchDir, globalFile, window, state.placement == WindowPlacement.Fullscreen)
            else App(globalFile, FileManager(window, inputFile), TextDrawer,
                state.placement == WindowPlacement.Fullscreen)
            }
        }
    }
}
