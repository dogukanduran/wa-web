// Fast headless exporter for recomp-kit: exports functions.tsv, strings.tsv,
// and functions/<addr>.asm without running the slow C decompiler on every function.
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.Reference;
import java.io.PrintWriter;
import java.nio.file.*;

public class ExportAsmFast extends GhidraScript {
    public void run() throws Exception {
        Path out = Path.of(getScriptArgs()[0], currentProgram.getName());
        Files.createDirectories(out.resolve("functions"));
        int total = 0;
        try (PrintWriter index = new PrintWriter(Files.newBufferedWriter(out.resolve("functions.tsv")));
             PrintWriter strings = new PrintWriter(Files.newBufferedWriter(out.resolve("strings.tsv")))) {
            index.println("address\tname\tbytes\tstatus\tmessage");
            DataIterator data = currentProgram.getListing().getDefinedData(true);
            while (data.hasNext()) {
                Data d = data.next();
                if (!(d.getValue() instanceof String)) continue;
                for (Reference r : getReferencesTo(d.getAddress())) {
                    Function caller = getFunctionContaining(r.getFromAddress());
                    strings.printf("%s\t%s\t%s\t%s%n", d.getAddress(), r.getFromAddress(),
                        caller == null ? "" : caller.getEntryPoint(),
                        d.getValue().toString().replace("\n", "\\n").replace("\t", "\\t"));
                }
            }
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext()) {
                monitor.checkCancelled();
                Function f = functions.next();
                String address = f.getEntryPoint().toString();
                try (PrintWriter asm = new PrintWriter(Files.newBufferedWriter(out.resolve("functions/" + address + ".asm")))) {
                    InstructionIterator instructions = currentProgram.getListing().getInstructions(f.getBody(), true);
                    while (instructions.hasNext()) {
                        Instruction ins = instructions.next();
                        asm.printf("%s  %s%n", ins.getAddress(), ins.toString());
                    }
                }
                index.printf("%s\t%s\t%d\tasm\t%n", address, f.getName(), f.getBody().getNumAddresses());
                total++;
                if (total % 1000 == 0) println("Exported " + total + " functions...");
            }
        }
        Files.writeString(out.resolve("summary.txt"), "discovered=" + total + "\n");
        println("Fast ASM export complete: " + total + " functions");
    }
}
