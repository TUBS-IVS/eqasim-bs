import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

import org.eqasim.braunschweig.BraunschweigConfigurator;
import org.eqasim.braunschweig.scenario.RunAdaptConfig;
import org.eqasim.core.scenario.config.GenerateConfig;
import org.matsim.api.core.v01.Scenario;
import org.matsim.core.config.CommandLine;
import org.matsim.core.config.Config;
import org.matsim.core.config.ConfigUtils;
import org.matsim.core.controler.Controler;
import org.matsim.core.controler.OutputDirectoryHierarchy.OverwriteFileSetting;
import org.matsim.core.scenario.ScenarioUtils;

import com.google.inject.Binding;
import com.google.inject.Injector;

/**
 * Scratch harness of the C5f investigation (not part of the repository): builds the controller injector of the
 * production configurator (BraunschweigConfigurator, as RunSimulation does and as ParkingInjectorTest builds it without the
 * parking module) with ONLY the standalone jar on the class path, and prints the number of bindings, a SHA-256 of their
 * sorted keys, and the provider methods without a source line.
 */
public class JarSmoke {
	public static void main(String[] args) throws Exception {
		Path output = Files.createTempDirectory("c5f_jar_smoke");

		CommandLine cmd = new CommandLine.Builder(new String[0]).allowAnyOption(true).build();
		BraunschweigConfigurator configurator = new BraunschweigConfigurator(cmd);
		Config config = ConfigUtils.createConfig();
		configurator.updateConfig(config);
		new GenerateConfig(cmd, "", 0.01, 0, 1).run(config);
		RunAdaptConfig.adaptConfiguration(config, "");
		config.setContext(output.resolve("config.xml").toUri().toURL());
		config.controller().setOutputDirectory(output.resolve("output").toString());
		config.controller().setOverwriteFileSetting(OverwriteFileSetting.deleteDirectoryIfExists);
		config.controller().setLastIteration(0);
		configurator.updateConfig(config);

		Scenario scenario = ScenarioUtils.createScenario(config);
		configurator.configureScenario(scenario);
		Controler controller = new Controler(scenario);
		configurator.configureController(controller);
		Injector injector = controller.getInjector();

		List<String> keys = injector.getBindings().keySet().stream().map(Object::toString).sorted()
				.collect(Collectors.toList());
		byte[] hash = MessageDigest.getInstance("SHA-256").digest(String.join("\n", keys).getBytes(StandardCharsets.UTF_8));
		StringBuilder hex = new StringBuilder();
		for (byte b : hash) {
			hex.append(String.format("%02x", b));
		}

		Pattern provider = Pattern.compile("Provides\\S*\\s+([\\w.$]+)\\.([\\w$<>]+)\\(([^)]*)\\)");
		Pattern resolved = Pattern.compile("[\\w$]+\\.java:\\d+");
		Map<String, String> methods = new TreeMap<>();
		for (Binding<?> binding : injector.getBindings().values()) {
			for (String text : new String[] { String.valueOf(binding), String.valueOf(binding.getProvider()),
					String.valueOf(binding.getSource()) }) {
				Matcher m = provider.matcher(text);
				while (m.find()) {
					methods.put(m.group(1) + "." + m.group(2), m.group(3));
				}
			}
		}
		long unresolved = methods.values().stream().filter(v -> !resolved.matcher(v).matches()).count();
		System.out.println("C5F_JAR_SMOKE bindings=" + keys.size() + " keys_sha256=" + hex + " provider_methods=" + methods.size()
				+ " without_source_line=" + unresolved);
		System.exit(0);
	}
}
