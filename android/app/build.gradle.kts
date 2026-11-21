import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// The phone app always carries the same version number as the PC app (sdj/__init__.py).
val appVersion: String = Regex("APP_VERSION\\s*=\\s*\"([^\"]+)\"")
    .find(rootProject.file("../sdj/__init__.py").readText())?.groupValues?.get(1) ?: "1.0.0"
val vParts = (appVersion.split(".").map { it.toIntOrNull() ?: 0 } + listOf(0, 0, 0)).take(3)
// Signing key: CI decrypts the encrypted key and sets these variables (see android/keystore/README.md).
val keyProps = Properties().apply {
    val path = System.getenv("SDJ_KEYSTORE_PATH")
    val pass = System.getenv("SDJ_KEYSTORE_PASSWORD")
    if (!path.isNullOrBlank() && !pass.isNullOrBlank()) {
        setProperty("storeFile", path); setProperty("storePassword", pass)
        setProperty("keyAlias", System.getenv("SDJ_KEY_ALIAS") ?: "sdj"); setProperty("keyPassword", pass)
    }
}

android {
    namespace = "com.sdj.mobilecare"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.sdj.mobilecare"
        minSdk = 26
        targetSdk = 35
        versionCode = vParts[0] * 10000 + vParts[1] * 100 + vParts[2]
        versionName = appVersion
    }

    signingConfigs {
        create("release") {
            if (!keyProps.isEmpty) {
                storeFile = file(keyProps.getProperty("storeFile"))
                storePassword = keyProps.getProperty("storePassword")
                keyAlias = keyProps.getProperty("keyAlias")
                keyPassword = keyProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
        freeCompilerArgs += listOf(
            "-opt-in=androidx.compose.material3.ExperimentalMaterial3Api",
            "-opt-in=androidx.compose.foundation.layout.ExperimentalLayoutApi",
            "-opt-in=androidx.compose.foundation.ExperimentalFoundationApi",
        )
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            all {
                it.maxHeapSize = "2g"
                it.systemProperty("sdj.out", rootProject.file("build/test-out").absolutePath)
            }
        }
    }
    packaging {
        resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.12.01")
    implementation(composeBom)
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    implementation("androidx.work:work-runtime-ktx:2.9.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.robolectric:robolectric:4.14.1")
    testImplementation("androidx.test:core:1.6.1")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.9.0")
    testImplementation(composeBom)
    testImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
