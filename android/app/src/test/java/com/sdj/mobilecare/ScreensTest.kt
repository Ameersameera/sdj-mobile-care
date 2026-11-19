package com.sdj.mobilecare

import android.graphics.Bitmap
import android.graphics.Canvas
import androidx.activity.ComponentActivity
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import com.sdj.mobilecare.data.Repo
import com.sdj.mobilecare.data.Store
import com.sdj.mobilecare.ui.AppRoot
import com.sdj.mobilecare.ui.AppTheme
import com.sdj.mobilecare.ui.Route
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File

/** Every screen is drawn with the sample shop; the pictures are kept as a CI artifact for review. */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34], qualifiers = "w400dp-h860dp-xhdpi")
class ScreensTest {
    @get:Rule
    val rule = createAndroidComposeRule<ComponentActivity>()

    @get:Rule
    val tmp = TemporaryFolder()

    private fun repo(signIn: Boolean = true, entries: Boolean = true): Repo = runBlocking {
        val r = testRepo(tmp.newFolder())
        r.load()
        if (signIn) r.signIn("admin123", true)
        if (entries) addSampleEntries(r)
        r
    }

    private fun show(name: String, start: Route, r: Repo = repo()) {
        rule.setContent { AppTheme { AppRoot(r, "Test phone", start) } }
        rule.waitForIdle()
        val view = rule.activity.window.decorView
        val bmp = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)
        view.draw(Canvas(bmp))
        val dir = File(Fixtures.out("shots").path).apply { mkdirs() }
        File(dir, "$name.png").outputStream().use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }
    }

    @Test fun link() = show("00_link", Route.Home, Repo(Store(tmp.newFolder()), OfflineNet(), Dispatchers.Unconfined, "t").also { runBlocking { it.load() } })
    @Test fun login() = show("01_login", Route.Home, repo(signIn = false, entries = false))
    @Test fun home() = show("02_home", Route.Home)
    @Test fun bills() = show("03_bills", Route.Sales)
    @Test fun newBill() = show("04_new_bill", Route.BillEdit())
    @Test fun billDetail() {
        val r = repo()
        show("05_bill_detail", Route.SaleView(r.state.value.sales.first { it.pending && it.lines.size == 1 && it.lines[0].imei.isNotEmpty() }.key), r)
    }
    @Test fun editBill() {
        val r = repo()
        show("06_edit_bill", Route.BillEdit(editKey = r.state.value.sales.first { it.pending && it.due > 0 }.key), r)
    }
    @Test fun stock() = show("07_stock", Route.Stock)
    @Test fun repairs() = show("08_repairs", Route.Jobs)
    @Test fun repairDetail() {
        val r = repo()
        show("09_repair_detail", Route.JobView(r.state.value.jobs.first { !it.pending }.key), r)
    }
    @Test fun newRepair() = show("10_new_repair", Route.JobNew)
    @Test fun customers() = show("11_customers", Route.Customers)
    @Test fun expenses() = show("12_expenses", Route.Expenses)
    @Test fun sync() = show("13_sync", Route.Sync)
    @Test fun more() = show("14_more", Route.More)
}
