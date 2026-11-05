plugins {
    kotlin("jvm") version "1.7.20"
    application
}
repositories { mavenCentral() }
dependencies { implementation("com.squareup.okio:okio-jvm:3.4.0") }
tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile> {
    kotlinOptions.jvmTarget = "17"
}
application { mainClass.set("MainKt") }
