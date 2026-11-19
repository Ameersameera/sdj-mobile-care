package com.sdj.mobilecare.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sdj.mobilecare.data.Approval
import com.sdj.mobilecare.data.MODE_LABELS
import com.sdj.mobilecare.data.Money
import com.sdj.mobilecare.data.PAY_MODES
import com.sdj.mobilecare.data.Repo
import com.sdj.mobilecare.data.UserError
import kotlinx.coroutines.launch

val Ink = Color(0xFF1D1F15)
val Gold = Color(0xFFD4AC4E)
val GoldSoft = Color(0xFFF4E7C5)
val Cream = Color(0xFFF7F3E8)
val Sand = Color(0xFFEFE7D4)
val Muted = Color(0xFF6B6656)
val Good = Color(0xFF1E7B34)
val Warn = Color(0xFFB26B00)
val Bad = Color(0xFFB42318)
val Info = Color(0xFF2A6FB8)

@Composable
fun AppTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = lightColorScheme(
            primary = Gold, onPrimary = Ink, primaryContainer = GoldSoft, onPrimaryContainer = Ink,
            secondary = Ink, onSecondary = Color.White, secondaryContainer = Sand, onSecondaryContainer = Ink,
            background = Cream, onBackground = Ink, surface = Color.White, onSurface = Ink,
            surfaceVariant = Sand, onSurfaceVariant = Muted, outline = Color(0xFFD6CBB2), error = Bad,
        ),
        content = content,
    )
}

@Composable
fun Section(title: String = "", modifier: Modifier = Modifier, trailing: @Composable (() -> Unit)? = null,
            content: @Composable ColumnScope.() -> Unit) {
    Card(modifier = modifier.fillMaxWidth(), shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White)) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (title.isNotEmpty() || trailing != null) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(title, fontWeight = FontWeight.Bold, fontSize = 16.sp, modifier = Modifier.weight(1f))
                    trailing?.invoke()
                }
            }
            content()
        }
    }
}

@Composable
fun Kpi(title: String, value: String, modifier: Modifier = Modifier, tone: Color = Ink, onClick: (() -> Unit)? = null) {
    Card(modifier = modifier.let { if (onClick != null) it.clickable { onClick() } else it }, shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White)) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
            Text(title, color = Muted, fontSize = 12.sp, maxLines = 1)
            Spacer(Modifier.height(2.dp))
            Text(value, color = tone, fontWeight = FontWeight.Bold, fontSize = 20.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

@Composable
fun Pill(text: String, color: Color = Info, modifier: Modifier = Modifier) {
    Box(modifier.background(color.copy(alpha = 0.12f), RoundedCornerShape(50)).padding(horizontal = 8.dp, vertical = 2.dp)) {
        Text(text, color = color, fontSize = 11.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
    }
}

@Composable
fun PendingPill() = Pill("Not synced yet", Warn)

@Composable
fun Empty(text: String) {
    Box(Modifier.fillMaxWidth().padding(32.dp), contentAlignment = Alignment.Center) {
        Text(text, color = Muted, fontSize = 14.sp)
    }
}

@Composable
fun SearchBox(value: String, onChange: (String) -> Unit, hint: String, modifier: Modifier = Modifier) {
    OutlinedTextField(value = value, onValueChange = onChange, placeholder = { Text(hint) }, singleLine = true,
        leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) }, modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp))
}

@Composable
fun MoneyField(label: String, paise: Long, onChange: (Long) -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    var text by remember { mutableStateOf(Money.plain(paise)) }
    LaunchedEffect(paise) { if ((Money.parse(text) ?: 0L) != paise) text = Money.plain(paise) }
    OutlinedTextField(
        value = text, enabled = enabled,
        onValueChange = { t ->
            val clean = t.filter { it.isDigit() || it == '.' }
            if (clean.count { it == '.' } <= 1 && clean.substringAfter('.', "").length <= 2) {
                text = clean
                onChange(Money.parse(clean) ?: 0)
            }
        },
        label = { Text(label) }, prefix = { Text("₹") }, singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal), modifier = modifier,
    )
}

@Composable
fun TextIn(label: String, value: String, onChange: (String) -> Unit, modifier: Modifier = Modifier, number: Boolean = false,
           phone: Boolean = false, singleLine: Boolean = true, password: Boolean = false) {
    OutlinedTextField(
        value = value, onValueChange = onChange, label = { Text(label) }, singleLine = singleLine, modifier = modifier,
        keyboardOptions = KeyboardOptions(keyboardType = when {
            password -> KeyboardType.Password
            phone -> KeyboardType.Phone
            number -> KeyboardType.Number
            else -> KeyboardType.Text
        }),
        visualTransformation = if (password) PasswordVisualTransformation() else androidx.compose.ui.text.input.VisualTransformation.None,
    )
}

@Composable
fun ModeChips(selected: String, onSelect: (String) -> Unit, modes: List<String> = PAY_MODES) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        modes.forEach { m -> FilterChip(selected = selected == m, onClick = { onSelect(m) }, label = { Text(MODE_LABELS[m] ?: m) }) }
    }
}

@Composable
fun Row2(left: String, right: String, bold: Boolean = false, color: Color = Ink) {
    Row(Modifier.fillMaxWidth()) {
        Text(left, modifier = Modifier.weight(1f), fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal, color = if (bold) color else Muted)
        Text(right, fontWeight = if (bold) FontWeight.Bold else FontWeight.Medium, color = color)
    }
}

@Composable
fun Busy() = CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)

@Composable
fun Confirm(title: String, text: String, ok: String = "Yes", onOk: () -> Unit, onDismiss: () -> Unit, danger: Boolean = false) {
    AlertDialog(onDismissRequest = onDismiss, title = { Text(title) }, text = { Text(text) },
        confirmButton = { TextButton(onClick = { onOk(); onDismiss() }) { Text(ok, color = if (danger) Bad else Ink, fontWeight = FontWeight.Bold) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel", color = Muted) } })
}

/** Owner / manager approves on this phone with their own password (checked offline against the PC's copy). */
@Composable
fun ApprovalDialog(repo: Repo, perms: Set<String>, onDone: (Approval) -> Unit, onDismiss: () -> Unit) {
    var user by remember { mutableStateOf("") }
    var pw by remember { mutableStateOf("") }
    var err by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Owner approval") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("This needs the owner or a manager: ${perms.joinToString { PERM_LABELS[it] ?: it }}. Ask them to type their username and password.", color = Muted, fontSize = 13.sp)
                TextIn("Username", user, { user = it })
                TextIn("Password", pw, { pw = it }, password = true)
                if (err.isNotEmpty()) Text(err, color = Bad, fontSize = 13.sp)
            }
        },
        confirmButton = {
            TextButton(enabled = !busy, onClick = {
                busy = true
                scope.launch {
                    try { onDone(repo.verifyApprover(user, pw, perms)) } catch (e: UserError) { err = e.message ?: "" } finally { busy = false }
                }
            }) { if (busy) Busy() else Text("Approve", fontWeight = FontWeight.Bold) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel", color = Muted) } },
    )
}

val PERM_LABELS = mapOf(
    "cancel_sale" to "cancel or edit a bill", "discount_override" to "a discount above the staff limit",
    "billing" to "make a bill", "quick_sale" to "quick sale", "collect_payment" to "collect a payment",
    "expenses" to "record an expense", "services" to "repair jobs",
)

@Composable
fun Gap(h: Int = 8) = Spacer(Modifier.height(h.dp))

@Composable
fun HGap(w: Int = 8) = Spacer(Modifier.width(w.dp))
