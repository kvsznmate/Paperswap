# R8 rules for the release build (isMinifyEnabled = true).
#
# Only reflection needs rules here. OkHttp, Coil, Compose and coroutines ship
# their own consumer rules inside their AARs.

# --- Gson: response/request models are filled by reflection --------------------
# Field names must survive because they are matched against @SerializedName,
# and the classes must keep their members because Gson instantiates them
# without calling a Kotlin constructor.
-keep class com.newsswipe.app.data.model.** { *; }
-keepattributes Signature, *Annotation*, InnerClasses, EnclosingMethod

# --- Retrofit 2.9 under R8 full mode (the AGP 8 default) ----------------------
# Retrofit 2.9's bundled rules predate full mode. These are the additions
# Retrofit 2.10+ ships: keep the generic types that suspend functions return
# through, or Retrofit fails at runtime with "Unable to create call adapter".
-keep,allowobfuscation,allowshrinking interface retrofit2.Call
-keep,allowobfuscation,allowshrinking class retrofit2.Response
-keep,allowobfuscation,allowshrinking class kotlin.coroutines.Continuation
-if interface * { @retrofit2.http.* public *** *(...); }
-keep,allowoptimization,allowshrinking,allowobfuscation class <3>
