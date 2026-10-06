// Runs the StructureMap twin (fhir/maps/type2-to-ema-cap-smpc-en.map, compiled into the
// repository's own package) on source Bundles, through the pinned HL7 validator's own transform
// engine, offline. test/official/structuremap-twin.test.ts launches it as a single source file on
// the pinned, checksummed validator_cli.jar, which holds every class it uses:
//
//   java -cp validator_cli.jar TwinRunner.java <validator version> <map url> <output directory>
//        <package or file>... -- <input>...
//
// It refuses to run on any validator version but the one named. The CLI's own `transform`
// refuses to run without a terminology server, so this builds the engine with none, loads each
// package or file named before `--` in that order, and transforms each input: the result is
// written as JSON to <output directory>/<input's name>, and a transform that throws (a check
// clause, or anything else) writes the message to <input's name>.refused instead. It exits 0
// once every input has one or the other, and 1 when it cannot start.

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;

import org.hl7.fhir.r5.elementmodel.Element;
import org.hl7.fhir.r5.elementmodel.JsonParser;
import org.hl7.fhir.r5.elementmodel.Manager.FhirFormat;
import org.hl7.fhir.r5.formats.IParser.OutputStyle;
import org.hl7.fhir.utilities.ByteProvider;
import org.hl7.fhir.utilities.VersionUtil;
import org.hl7.fhir.validation.ValidationEngine;

public class TwinRunner {
  // fetchResource(Class, String) is deprecated in 6.10.4 and still the one lookup by URL.
  @SuppressWarnings("deprecation")
  public static void main(String[] args) throws Exception {
    List<String> arguments = Arrays.asList(args);
    int separator = arguments.indexOf("--");
    if (args.length < 4 || separator < 3) {
      System.err.println("usage: TwinRunner <validator version> <map url> <output directory> <package>... -- <input>...");
      System.exit(1);
    }
    if (!VersionUtil.getVersion().equals(args[0])) {
      System.err.println("validator " + VersionUtil.getVersion() + " is not the pinned " + args[0]);
      System.exit(1);
    }
    String map = args[1];
    Path output = Path.of(args[2]);
    ValidationEngine engine = new ValidationEngine.ValidationEngineBuilder()
        .withVersion("5.0.0")
        .withNoTerminologyServer()
        .fromSource("hl7.fhir.r5.core#5.0.0");
    for (String source : arguments.subList(3, separator)) {
      engine.getIgLoader().loadIg(engine.getIgs(), engine.getBinaries(), source, false);
    }
    if (engine.getContext().fetchResource(org.hl7.fhir.r5.model.StructureMap.class, map) == null) {
      System.err.println("no StructureMap " + map + " in the packages loaded");
      System.exit(1);
    }
    for (String name : arguments.subList(separator + 1, arguments.size())) {
      Path input = Path.of(name);
      String file = input.getFileName().toString();
      try {
        Element result = engine.transform(ByteProvider.forFile(input.toFile()), FhirFormat.JSON, map);
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        new JsonParser(engine.getContext()).compose(result, bytes, OutputStyle.NORMAL, null);
        Files.write(output.resolve(file), bytes.toByteArray());
      } catch (Exception error) {
        String message = error.getClass().getName() + ": " + error.getMessage();
        Files.writeString(output.resolve(file + ".refused"), message, StandardCharsets.UTF_8);
      }
    }
  }
}
