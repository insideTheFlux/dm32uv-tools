// Round-trip test harness for the DM-32UV settings extension.
// Emulates what DM32UV::upload() does: the radio's current codeplug is loaded into the codeplug
// object, then the configuration is encoded on top of it.
#include <QCoreApplication>
#include <QFile>
#include <QTextStream>
#include <QMetaObject>
#include <QMetaProperty>
#include <QMetaEnum>
#include <iostream>
#include <memory>

#include "config.hh"
#include "dm32uv_codeplug.hh"
#include "dm32uv_extension.hh"
#include "radiosettings.hh"
#include "logger.hh"

static bool decodeFile(DM32UVCodeplug &cp, Config &cfg, const QString &bin, ErrorStack &err) {
  if (! cp.read(bin, err)) { std::cerr << "read failed: " << err.format().toStdString() << "\n"; return false; }
  if (! cp.decode(&cfg, err)) { std::cerr << "decode failed: " << err.format().toStdString() << "\n"; return false; }
  if (! cp.postprocess(&cfg, err)) { std::cerr << "postprocess failed: " << err.format().toStdString() << "\n"; return false; }
  return true;
}

// encode cfg on top of the codeplug that was loaded from `bin`, write `out`
static bool encodeOnto(const QString &bin, Config &cfg, const QString &out, ErrorStack &err) {
  DM32UVCodeplug cp;
  if (! cp.read(bin, err)) { std::cerr << "read failed\n"; return false; }
  std::unique_ptr<Config> inter(cp.preprocess(&cfg, err));
  if (! inter) { std::cerr << "preprocess failed: " << err.format().toStdString() << "\n"; return false; }
  Codeplug::Flags flags; flags.setUpdateCodeplug(true);
  if (! cp.encode(inter.get(), flags, err)) { std::cerr << "encode failed: " << err.format().toStdString() << "\n"; return false; }
  cp.image(0).sort();
  if (! cp.write(out, err)) { std::cerr << "write failed: " << err.format().toStdString() << "\n"; return false; }
  return true;
}

static bool setProp(QObject *obj, const QString &name, const QString &value) {
  const QMetaObject *mo = obj->metaObject();
  int idx = mo->indexOfProperty(name.toLatin1().constData());
  if (idx < 0) { std::cerr << "no property " << name.toStdString() << "\n"; return false; }
  QMetaProperty p = mo->property(idx);
  if (p.isEnumType()) {
    bool ok = false; int v = p.enumerator().keyToValue(value.toLatin1().constData(), &ok);
    if (! ok) { std::cerr << "bad enum value " << value.toStdString() << " for " << name.toStdString() << "\n"; return false; }
    return p.write(obj, QVariant(v));
  }
  if (QMetaType::Bool == p.typeId()) return p.write(obj, QVariant(value == "true"));
  if (QMetaType::UInt == p.typeId()) return p.write(obj, QVariant(value.toUInt()));
  if (QMetaType::Int == p.typeId()) return p.write(obj, QVariant(value.toInt()));
  std::cerr << "unsupported type\n"; return false;
}

int main(int argc, char **argv) {
  QCoreApplication app(argc, argv);
  if (argc < 4) { std::cerr << "usage: rt (noext|ext|set|toyaml|fromyaml) in.bin out [key=value ...]\n"; return 2; }
  QString mode = argv[1], in = argv[2], out = argv[3];
  ErrorStack err;

  if (mode == "toyaml") {                  // decode and write YAML
    DM32UVCodeplug cp; Config cfg;
    if (! decodeFile(cp, cfg, in, err)) return 1;
    QFile f(out); f.open(QIODevice::WriteOnly); QTextStream s(&f);
    if (! cfg.toYAML(s, err)) { std::cerr << "toYAML failed: " << err.format().toStdString() << "\n"; return 1; }
    return 0;
  }
  if (mode == "fromyaml") {                // out = encode(YAML) on top of in.bin; argv[4] = YAML
    Config cfg;
    if (! cfg.readYAML(argv[4], err)) { std::cerr << "readYAML failed: " << err.format().toStdString() << "\n"; return 1; }
    return encodeOnto(in, cfg, out, err) ? 0 : 1;
  }
  DM32UVCodeplug cp; Config cfg;
  if (! decodeFile(cp, cfg, in, err)) return 1;
  if (mode == "noext") {                    // baseline: behaves like the unpatched QDMR
    cfg.settings()->setDM32UVExtension(nullptr);
  } else if (mode == "set") {               // change extension properties
    DM32UVSettingsExtension *e = cfg.settings()->dm32uvExtension();
    if (! e) { std::cerr << "no extension after decode!\n"; return 1; }
    for (int i = 4; i < argc; i++) {
      QString kv = argv[i]; int eq = kv.indexOf('=');
      if (eq < 0 || ! setProp(e, kv.left(eq), kv.mid(eq + 1))) return 1;
    }
  } else if (mode != "ext") { std::cerr << "unknown mode\n"; return 2; }
  return encodeOnto(in, cfg, out, err) ? 0 : 1;
}
