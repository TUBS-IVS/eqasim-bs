import java.nio.file.*;
import java.util.*;
import org.eqasim.braunschweig.parking.*;
public class DiffCheck {
    public static void main(String[] args) throws Exception {
        ParkingTariffs tariffs = ParkingTariffs.read(Path.of(args[0]));
        int n = 0, bad = 0;
        Map<String, Integer> outcomes = new TreeMap<>();
        for (String line : Files.readAllLines(Path.of(args[1]))) {
            String[] f = line.split(",");
            ZoneTariff t = tariffs.zone(f[0]).orElseThrow();
            ParkingCostCalculator.Result r = ParkingCostCalculator.cents(t, Double.parseDouble(f[1]), Double.parseDouble(f[2]), f[3],
                Boolean.parseBoolean(f[4]), Boolean.parseBoolean(f[5]));
            n++;
            outcomes.merge(f[7], 1, Integer::sum);
            if (r.cents() != Long.parseLong(f[6]) || !r.outcome().name().equals(f[7])) {
                if (bad++ < 5) System.out.println("MISMATCH " + line + " java=" + r);
            }
        }
        System.out.println("cases " + n + ", mismatches " + bad + ", outcomes " + outcomes);
    }
}
